   try:
        names = ", ".join(c["name"] for c in get_designated_countries())
    except Exception:
        # A broken/missing designated-countries source must not take down every
        # tree's prompts: only sensitive_charities consumes this token, and its
        # compute node fails safe (material Missing Information) on its own.
        names = ""
    return {"designated_countries_list": names}




from abc import ABC
from typing import Callable

from langgraph.graph import END, START, StateGraph

from main.riskflag_detection.countries import get_designated_countries
from main.riskflag_detection.scap_tree import SCAPGraph
from main.riskflag_detection.utils import (
    SIAPState,
    get_scap2_flag,
    llm_prompter,
    llm_prompter_full,
    output_format_mapping,
    TextAnswer,
    result_fetcher,
)

def extra_placeholders() -> dict:
    """Placeholders beyond {activity} that tree prompts may reference (e.g. the
    designated-country list injected into sensitive_charities' public-information
    check). Built per call so list updates are picked up without a restart;
    str.format ignores unused kwargs, so trees that don't use them are unaffected."""
    try:
        names = ", ".join(c["name"] for c in get_designated_countries())
    except Exception:
        # A broken/missing designated-countries source must not take down every
        # tree's prompts: only sensitive_charities consumes this token, and its
        # compute node fails safe (material Missing Information) on its own.
        names = ""
    return {"designated_countries_list": names}


class SIAPTree(ABC):

    def __init__(self, info: dict, function_mapping: dict):
        self.name = info["name"]
        self.field = info["field"] if "field" in info else ""

        # Category-specific compute handlers (e.g. sensitive_charities'
        # designated_country_check) are registered in utils.function_mapping,
        # not here — the engine stays category-agnostic.
        function_mapping.update(
            {
                "scap_check": lambda s: self.scap_check_node(s),
                "scap_check_charities": lambda s: self.scap_check_node(
                    s, is_charity=True
                ),
            }
        )

        self.graph = self.load_graph_from_json(info, function_mapping).compile()

    def load_graph_from_json(self, info: dict, function_mapping: dict):
        graph = StateGraph(SIAPState)

        graph.add_edge(START, info["start"])

        result_nodes = {
            "siap": self.siap,
            "exit": self.exit_node,
            "missing_information": self.missing_information,
            "prohibited_relation": self.prohibited_relation,
            "cooled_down_siap": self.cooled_down_siap,
        }

        for label, pointer in result_nodes.items():
            graph.add_node(label, pointer)
            graph.add_edge(label, END)

        for label, node_info in info["nodes"].items():
            pointer = None
            transitions = node_info["transitions"]

            if node_info["type"] == "prompt_chain":
                graph.add_node(label, lambda state: state)
                graph.add_edge(label, f"{label}_1")

                for idx, prompt in enumerate(node_info["prompts"], start=1):
                    is_last: bool = idx == len(node_info["prompts"])

                    subnode_label = f"{label}_{idx}"
                    pointer = self.prompt_node(
                        subnode_label,
                        prompt,
                        output_format_mapping[node_info["output_format"]],
                    )

                    subnode_transitions = transitions.copy()
                    if not is_last:
                        for key in node_info["chain_key"]:
                            subnode_transitions[key] = f"{label}_{idx+1}"

                    graph.add_node(subnode_label, pointer)
                    fetcher = result_fetcher
                    if label == "verification" and is_last:
                        fetcher = self.verification_result_fetcher
                    graph.add_conditional_edges(
                        subnode_label, fetcher, subnode_transitions
                    )

            else:
                if node_info["type"] == "prompt":
                    pointer = self.prompt_node(
                        label,
                        node_info["text"],
                        output_format_mapping[node_info["output_format"]],
                    )
                else:
                    pointer = function_mapping[label]

                graph.add_node(label, pointer)
                graph.add_conditional_edges(label, result_fetcher, transitions)

        return graph

    def verification_result_fetcher(self, state: SIAPState):
        last = result_fetcher(state)
        if last != "No":
            return last

        has_missing = any(
            v == "Missing Information" and k.startswith("verification_")
            for output in state["node_outputs"]
            for k, v in output.items()
        )
        return "Missing Information" if has_missing else "No"

    def prompt_helper(
        self, state: SIAPState, node_label: str, prompt: str, output_format: str
    ) -> SIAPState:
        formatted_question = prompt.format(
            activity=f'"{state["client_activity"]}"',
            **extra_placeholders(),
        )
        llm_result = llm_prompter_full(
            state,
            formatted_question,
            output_format,
        )

        llm_answer = llm_result["answer"]
        state["node_outputs"].append({node_label: llm_answer})
        if llm_answer == "Missing Information":
            reasoning = llm_result.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                reason = reasoning.strip()
            else:
                reason = f"Insufficient information to answer: {formatted_question}"
            state["missing_info_reasons"] = state.get("missing_info_reasons", []) + [
                reason
            ]

        return state

    def prompt_node(
        self, node_label: str, prompt: str, output_format: str
    ) -> Callable[[SIAPState], SIAPState]:
        return lambda s: self.prompt_helper(s, node_label, prompt, output_format)

    def exit_node(self, state: SIAPState) -> SIAPState:
        state["result"] = "Not SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def missing_information(self, state: SIAPState) -> SIAPState:
        state["result"] = "Missing Information"
        reasons = state.get("missing_info_reasons", [])
        unique_reasons = []
        seen = set()
        for reason in reasons:
            if reason not in seen:
                unique_reasons.append(reason)
                seen.add(reason)

        reasons_text = (
            "; ".join(unique_reasons)
            if unique_reasons
            else "Client notes do not contain enough information to reach a determination."
        )
        prompt = "\n".join(
            [
                "Rewrite the following missing-information reasons into a single, concise justification (1-2 sentences).",
                "Use professional KYC/AML analyst language.",
                "Do not introduce assumptions.",
                "Do not add facts not present in the reasons.",
                "",
                "Reasons:",
                f"<<<\n{reasons_text}\n>>>",
            ]
        )
        summary = llm_prompter(state, prompt, TextAnswer)
        state["missing_info_summary"] = (
            summary.strip()
            if isinstance(summary, str) and summary.strip()
            else reasons_text
        )

        return state

    def siap(self, state: SIAPState) -> SIAPState:
        state["result"] = "SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def cooled_down_siap(self, state: SIAPState) -> SIAPState:
        state["result"] = "Cooled Down SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def prohibited_relation(self, state: SIAPState) -> SIAPState:
        state["result"] = "Prohibited Relation"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def scap_check_node(self, state: SIAPState, is_charity: bool = False):
        tag = "scap_check" if not is_charity else "scap_check_charity"

        resulting_state = SCAPGraph(true_if_charity=is_charity).invoke(
            state["client_notes"], state["client_activity"]
        )
        mapping = {
            "SCAP": "Yes",
            "No SCAP": "No",
            "Missing Information": "Missing Information",
        }

        resulting_state = get_scap2_flag(resulting_state)

        state["node_outputs"].append({tag: mapping[resulting_state["scap2_flag"][0]]})

        return state
