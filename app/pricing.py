"""Fee estimates from the spec's Consolidated Financial Matrix (Section 7).

Deterministic lookup — never the LLM. Values live here (editable); a per-tenant override can
be layered on later. estimate() returns the human-readable line the bot shows at completion.
"""

PRICING = {
    "personal_base": {"initial": 45, "range": "$60–70"},
    "personal_gig": {"initial": 70, "range": "$75–150+, volume scaled"},
    "gst_setup": {"flat": 85},
    "gst_return": {"per_year": 50},
    "corporate": {"initial": 275, "range": "$275–700 based on volume"},
    "incorporation": {"flat": 350, "breakdown": "Corp $100 + Govt $200 + GST $50; named fees vary"},
    "renewal": {"flat": 62, "breakdown": "$12 Govt + $50 Service"},
    "noa": {"per": 75},
    "post_slip_charge": 65,   # adding forgotten slips after the file is completed
}


def estimate(service: str, answers: dict, prices: dict = PRICING) -> str:
    if service == "Personal Tax":
        if answers.get("is_gig") == "Yes":
            p = prices["personal_gig"]
            return f"Estimated fee: Personal Tax + Gig Economy — ${p['initial']} initial ({p['range']})."
        p = prices["personal_base"]
        return f"Estimated fee: Personal Tax — ${p['initial']} initial (standard, up to 3 slips; {p['range']})."

    if service == "GST/HST":
        if answers.get("gst_service") == "File a GST Return":
            return f"Estimated fee: GST Return Filing — ${prices['gst_return']['per_year']} per filing year."
        return f"Estimated fee: GST Number Registration — ${prices['gst_setup']['flat']} flat."

    if service == "Corporate Tax":
        p = prices["corporate"]
        return f"Estimated fee: Corporate Tax Filing — ${p['initial']} retainer ({p['range']})."

    if service == "Business Registration":
        if answers.get("reg_type") == "Annual Renewal":
            p = prices["renewal"]
            return f"Estimated fee: Annual Renewal — ${p['flat']} flat ({p['breakdown']})."
        p = prices["incorporation"]
        return f"Estimated fee: Company Incorporation — ${p['flat']} flat ({p['breakdown']})."

    return ""
