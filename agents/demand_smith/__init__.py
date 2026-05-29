"""DemandSmith — FMC Part 541 dispute letter drafting agent.

Public interface: draft_letter(verdict, score, case, filing_date=None) -> DemandLetter
Framework: LangChain 0.3.x (ChatPromptTemplate + shared get_llm_client)
"""

from agents.demand_smith.demand_smith import draft_letter

__all__ = ["draft_letter"]
