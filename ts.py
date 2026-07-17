{
  "name": "Sensitive Charities and Non-Profit Organizations (NPOs)",
  "description": "Assessment of charities/NPOs as Sensitive Charity/NPO SIAP: charitable purpose, recipients vs donors/members, third-party funding, and designated-country nexus (established in, or 25% or more of inflow/outflow value). Employees, family philanthropic vehicles, and listed institutional charities are excluded.",
  "field": "charities, charitable organizations, non-profit organizations, foundations, humanitarian organizations, aid organizations, donation-based entities",
  "comment": "Cooldown block (final_status_check/cooled_down_check) removed: the sensitive-charities guidance defines no cooldown for this category (outcomes are EDD approval or exit/UBR) and the imported ownership/earn-out wording is meaningless for NPOs. Pending policy-owner confirmation that no framework-level cooldown applies.",
  "placeholders": {
    "designated_countries_list": "(designated-country list not injected — production must supply the current '**' countries from the Financial Crime Country Risk Information Table, comma-joined, at runtime)"
  },
  "assessment_level": "client",
  "start": "involvement",
  "nodes": {
    "involvement": {
      "type": "prompt",
      "text": "Does the client, as an organization or through his activity, primarily engage in raising or disbursing funds for charitable, religious, cultural, educational, social or fraternal purposes, or for carrying out other types of 'good works' (FATF charity/NPO definition)? Answer Yes if any such activity is described. Answer No if the activity is clearly unrelated to charitable or non-profit work. Answer Missing Information if the description is too unclear to decide. Client notes: {activity}",
      "summary": "Charity/NPO involvement - does the client raise or disburse funds for charitable/religious/cultural/educational/social/fraternal purposes?",
      "output_format": "ternary",
      "transitions": {
        "Yes": "employee_check",
        "No": "exit",
        "Missing Information": "missing_information"
      },
      "rationale": {
        "No": "no_nexus",
        "Missing Information": "unclear_nexus"
      }
    },

    "employee_check": {
      "type": "prompt",
      "text": "Is the client an individual (natural person) whose connection to the charity/NPO described is that of an employee, senior executive manager, or board member — rather than the client being the charity/NPO organization itself? Answer Yes for such individuals: the policy excludes employees of Sensitive Charities/NPOs, including senior executive managers and board members, from this SIAP category. Answer No if the client IS the charity/NPO (a legal person, arrangement or organization). Answer Missing Information if the client's relationship to the charity cannot be determined. Client notes: {activity}",
      "summary": "Employee exclusion - individuals working for a charity (incl. senior execs and board members) are out of scope; only the charity/NPO itself qualifies",
      "output_format": "ternary",
      "transitions": {
        "Yes": "exit",
        "No": "non_siap_check",
        "Missing Information": "non_siap_check"
      },
      "rationale": {
        "Yes": "exemption"
      },
      "comment": "Policy 2.2 note: employees incl. senior executive managers and board members are NOT in scope — the opposite polarity of the art-dealing/professional-sport categories. MI continues so an ambiguous dossier is assessed as if the client were the charity."
    },

    "non_siap_check": {
      "type": "prompt_chain",
      "output_format": "ternary",
      "chain_key": [
        "No",
        "Missing Information"
      ],
      "transitions": {
        "Yes": "exit",
        "No": "involvement_check",
        "Missing Information": "involvement_check"
      },
      "prompts": [
        "Is the client organization a well-known institutional charity/NPO (incl. its branches) listed on the Sensitive Charities Exclusion List (e.g. Amnesty International, Bill & Melinda Gates Foundation, Doctors Without Borders/Medecins Sans Frontieres, The International Red Cross and Red Crescent Movement)? Answer from the client notes and the organization's name: {activity}",
        "Is the client organization subordinated to the United Nations (UN) and listed as one of its Funds and Programs or Specialized Agencies? Client notes: {activity}",
        "Is the client the UBS Optimus Foundation? Client notes: {activity}",
        "Is the client a trust, foundation, domiciliary company or other legal entity that is established AND funded by an individual or a limited group of individuals (such as family members) for charitable purposes — i.e. a private/family philanthropic vehicle that does not raise funds from the wider public? Client notes: {activity}"
      ],
      "rationale": {
        "Yes": "exemption"
      },
      "comment": "Exclusion List and UN membership are exclusive external lists (policy 2.3): production should verify membership via a structured lookup against the maintained lists; these prompts are the documented fallback. Fourth limb implements the family/private-vehicle exclusion (policy 2.2 note) explicitly."
    },

    "involvement_check": {
      "type": "prompt_chain",
      "output_format": "ternary",
      "chain_key": [
        "Yes"
      ],
      "transitions": {
        "Yes": "designated_country_check",
        "No": "exit",
        "Missing Information": "missing_information"
      },
      "prompts": [
        "Based on the client notes, is the client an organization that pursues a charitable purpose (incl. but not limited to relieving poverty, education, religion, protecting the environment, animal welfare, human rights and community development)? Client notes: {activity}",
        "Based on the client notes, are the recipients of the charitable benefits a clearly different population from the charity's donors and members (e.g. patients, aid recipients, children supported by the charity)? An explicit statement that recipients are not donors is NOT required — answer Yes when the described beneficiaries are plainly distinct from the donors and members. Answer No if the notes indicate that benefits flow back to the charity's own donors or members (e.g. a members' mutual-benefit association). Answer Missing Information only if the beneficiary population cannot be identified from the notes at all. Client notes: {activity}",
        "Based on the client notes, are the funds for the charitable activities raised from third parties such as the general public, private or commercial company donors, public sources including government and supra-government organizations and their agencies (such as international development aid agencies), or other charities and non-profit organizations? Client notes: {activity}"
      ],
      "rationale": {
        "Yes": "identified_nexus",
        "No": "no_nexus",
        "Missing Information": "unclear_nexus"
      },
      "comment": "Policy criteria 1-3 (cumulative): chain_key ['Yes'] gives AND-semantics — any No exits, any MI surfaces as missing information. Runs BEFORE the designated-country check so a definite non-charity is never masked by an unknown country nexus. Limb 2 reworded (2026-07-16, Cleft-Children case): the old 'neither donors nor members' phrasing demanded an explicit negative no dossier ever states, MI-ing every real charity; it now asks whether beneficiaries are plainly a different population. All limbs end with 'Client notes: {activity}' — the old mid-sentence insertion garbled the question when {activity} carried a source-of-wealth fragment."
    },

    "designated_country_check": {
      "type": "compute",
      "summary": "Designated-country check (criterion 4, limbs 1-3): LLM extracts the charity's countries and flow shares; code looks them up against the '**' list and applies the 25% thresholds",
      "transitions": {
        "Yes": "siap",
        "No": "public_connection_check",
        "Missing Information": "public_connection_check_after_mi"
      },
      "rationale": {
        "Yes": "confirmed_siap"
      },
      "comment": "Backed by utils.designated_country_check_node (registered in function_mapping); designated country with unstated share, or countries not determinable -> Missing Information (material), never exit."
    },

    "public_connection_check": {
      "type": "prompt",
      "text": "Based on publicly known information about this charity/NPO's work and projects (beyond the client notes), does it have a direct or indirect substantial connection (25% or more of inflow or outflow value) to a designated country? The current designated countries are: {designated_countries_list}. Answer Missing Information if no such public information is known. Client notes: {activity}",
      "summary": "Public-information connection check (criterion 4, limb 4) - policy note: publicly known projects/work must be considered; not answerable by any look-up",
      "output_format": "ternary",
      "transitions": {
        "Yes": "siap",
        "No": "exit",
        "Missing Information": "missing_information"
      },
      "rationale": {
        "Yes": "confirmed_siap",
        "No": "no_nexus",
        "Missing Information": "unclear_siap"
      },
      "comment": "Reached when the look-up answered No: countries were stated and none is designated, so a No here exits cleanly. Stays a prompt — the policy requires considering public information, which no table answers; the injected list tells the model what counts as designated."
    },

    "public_connection_check_after_mi": {
      "type": "prompt",
      "text": "Based on publicly known information about this charity/NPO's work and projects (beyond the client notes), does it have a direct or indirect substantial connection (25% or more of inflow or outflow value) to a designated country? The current designated countries are: {designated_countries_list}. Answer Missing Information if no such public information is known. Client notes: {activity}",
      "summary": "Public-information connection check (post-MI variant) - an inconclusive look-up must surface as Missing Information, not exit",
      "output_format": "ternary",
      "transitions": {
        "Yes": "siap",
        "No": "missing_information",
        "Missing Information": "missing_information"
      },
      "rationale": {
        "Yes": "confirmed_siap",
        "No": "unclear_siap",
        "Missing Information": "unclear_siap"
      },
      "comment": "Same prompt as public_connection_check but reached when the look-up returned Missing Information (countries or shares not determinable from the notes). A No here must NOT exit — the notes-based country picture is still unresolved — so both No and MI surface as missing_information. Duplicated node rather than shared because the correct No-routing depends on how it was reached."
    }
  }
}
