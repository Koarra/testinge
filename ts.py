# Reference copy: ADDITIONS to main/riskflag_detection/utils.py (the module
# already holding SIAPState, function_mapping, llm_prompter_full, etc.).
#
# Sensitive-charities designated-country check (policy criterion 4, limbs 1-3).
# Category-specific logic kept out of the generic SIAPTree engine: register the
# node here in function_mapping; the label is referenced solely by the
# sensitive_charities tree. The LLM extracts countries and flow shares from the
# client notes; all decisions (designation membership, the '25% or more'
# threshold) are made in plain code against the current designated-country list.

from typing import List, Optional

from langchain.messages import HumanMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from main.constants import AZURE_OPENAI_LLM_CONFIG
from main.riskflag_detection.countries import get_designated_countries


class CountryRef(BaseModel):
    country: str = Field(
        description="Country name, using the most recent ISO 3166 English name"
    )
    iso_alpha2: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-2 code (2 letters, e.g. 'SY'); null if unsure",
    )


class CountryFlowShare(CountryRef):
    share_pct: Optional[float] = Field(
        default=None,
        description="Share of the charity's total inflow/outflow value, as a "
        "number from 0 to 100 (e.g. 40 for 40%), if stated in the notes; "
        "null when no share is stated",
    )


class CharityCountryExtraction(BaseModel):
    establishment_countries: List[CountryRef] = Field(
        description="Countries where the charity/NPO is registered, headquartered "
        "or constituted; empty if not determinable from the notes"
    )
    inflow_shares: List[CountryFlowShare] = Field(
        description="Countries the charity receives funding from (donations, "
        "grants, funding), with the % of total inflow value where stated"
    )
    outflow_shares: List[CountryFlowShare] = Field(
        description="Countries the charity disburses funds to (projects, grants "
        "made, programs), with the % of total outflow value where stated"
    )
    countries_determinable: bool = Field(
        description="False when the notes do not allow determining the charity's "
        "countries at all"
    )


CHARITY_COUNTRY_EXTRACTION_PROMPT = """\
You are an expert compliance officer. Extract ONLY the geographic facts below from
the client notes — do not judge whether any country is sensitive or designated.

1. establishment_countries: where the charity/NPO is registered, headquartered or
   constituted (its domicile / place of establishment).
2. inflow_shares: each country the charity receives funding from (donations,
   grants, funding), with the stated % of total inflow value; leave share_pct null
   when no share is stated.
3. outflow_shares: each country the charity disburses funds to (projects, grants
   made, programs), with the stated % of total outflow value; leave share_pct null
   when no share is stated.
Express every share as a number from 0 to 100 (e.g. 40 for 40%), never as a
fraction. Count indirect flows (funds routed via intermediaries) toward the
ultimate origin or destination country where the notes make it clear. Use the
most recent ISO 3166 English country names, and give each country's ISO 3166-1
alpha-2 code (2 letters); leave the code null if unsure. Set
countries_determinable to false when the notes do not allow determining the
countries at all.

Client notes:
{client_notes}

Activity under assessment:
{activity}
"""

_MI_DETAILS = {
    "designated_share_not_stated": (
        "MATERIAL missing information: {countries} appears in the charity's fund "
        "flows and is on the designated-country list, but the share of "
        "inflow/outflow value linked to it is not stated — the 25% threshold "
        "cannot be confirmed."
    ),
    "countries_not_determinable": (
        "MATERIAL missing information: the charity's countries of establishment "
        "and fund flows cannot be determined from the notes, so the "
        "designated-country check cannot be assessed."
    ),
    "extraction_failed": (
        "MATERIAL missing information: the country extraction failed, so the "
        "designated-country check could not be assessed."
    ),
    "designated_list_unavailable": (
        "MATERIAL missing information: the designated-country list could not be "
        "loaded, so the designated-country check could not be assessed."
    ),
}


def _finish_designated_country_check(
    state, answer: str, reason: str, unknown_designated: List[str]
):
    # Reason and materiality first, answer last: result_fetcher routes on the
    # LAST entry in node_outputs, so the final append must be the Yes/No/MI answer.
    state["node_outputs"].append({"designated_country_check_reason": reason})
    if answer == "Missing Information":
        state["node_outputs"].append(
            {"designated_country_check_materiality": "material"}
        )
        detail = _MI_DETAILS[reason].format(countries=", ".join(unknown_designated))
        state["missing_info_reasons"] = state.get("missing_info_reasons", []) + [
            detail
        ]
    state["node_outputs"].append({"designated_country_check": answer})
    return state


def designated_country_check_node(state):
    """Compute node for the sensitive_charities tree (state: SIAPState).

    Fail safe, never crash: this node's failures become material Missing
    Information — an already-screened charity must surface, not error out.
    """
    try:
        rows = get_designated_countries()
        designated_codes = {r["code"].strip().upper() for r in rows}
        designated_names = {r["name"].strip().lower() for r in rows}
    except Exception:
        return _finish_designated_country_check(
            state, "Missing Information", "designated_list_unavailable", []
        )

    try:
        llm = AzureChatOpenAI(**AZURE_OPENAI_LLM_CONFIG).with_structured_output(
            CharityCountryExtraction
        )
        extraction: CharityCountryExtraction = llm.invoke(
            [
                HumanMessage(
                    CHARITY_COUNTRY_EXTRACTION_PROMPT.format(
                        client_notes=state["client_notes"],
                        activity=state["client_activity"],
                    )
                )
            ]
        )
    except Exception:
        return _finish_designated_country_check(
            state, "Missing Information", "extraction_failed", []
        )

    # Guard: a 'determinable' claim with nothing extracted is not determinable.
    if extraction.countries_determinable and not (
        extraction.establishment_countries
        or extraction.inflow_shares
        or extraction.outflow_shares
    ):
        extraction.countries_determinable = False

    # Guard: shares strictly between 0 and 1 are almost certainly fractions
    # (0.4 emitted for 40%). Trusting them would silently under-flag (0.4 >= 25
    # is False), so treat them as unstated — a designated country then surfaces
    # as material MI instead of a wrong No.
    for f in extraction.inflow_shares + extraction.outflow_shares:
        if f.share_pct is not None and 0 < f.share_pct < 1:
            f.share_pct = None

    def is_designated(ref: CountryRef) -> bool:
        # ISO code is the primary key (immune to naming variants such as
        # 'Syria' vs 'Syrian Arab Republic'); name match is the fallback
        # when the extraction could not supply a code.
        if ref.iso_alpha2 and ref.iso_alpha2.strip().upper() in designated_codes:
            return True
        return ref.country.strip().lower() in designated_names

    if any(is_designated(c) for c in extraction.establishment_countries):
        return _finish_designated_country_check(
            state, "Yes", "established_in_designated_country", []
        )

    for limb, flows in (
        ("designated_country_inflows", extraction.inflow_shares),
        ("designated_country_outflows", extraction.outflow_shares),
    ):
        if any(
            f.share_pct is not None and f.share_pct >= 25
            for f in flows
            if is_designated(f)
        ):
            return _finish_designated_country_check(state, "Yes", limb, [])

    if not extraction.countries_determinable:
        return _finish_designated_country_check(
            state, "Missing Information", "countries_not_determinable", []
        )

    # The designation look-up runs on every extracted country whether or not a
    # share is stated; shares only decide Yes vs material-MI.
    unknown_designated = sorted(
        {
            f.country
            for f in extraction.inflow_shares + extraction.outflow_shares
            if f.share_pct is None and is_designated(f)
        }
    )
    if unknown_designated:
        # A designated country appears but its share is not stated: MATERIAL
        # missing information — this one fact separates the case from SIAP.
        return _finish_designated_country_check(
            state,
            "Missing Information",
            "designated_share_not_stated",
            unknown_designated,
        )

    # Countries are known and none is designated. Any missing shares here are
    # NON-MATERIAL (they could not change the outcome): a No, not MI.
    return _finish_designated_country_check(
        state, "No", "no_designated_country_nexus", []
    )


# Register in the EXISTING function_mapping dict defined in this module:
#
#     function_mapping = {
#         ...,
#         "designated_country_check": designated_country_check_node,
#     }
