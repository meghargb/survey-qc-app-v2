"""
AI integration layer.

Supports either Anthropic (Claude) or Google (Gemini) - set AI_PROVIDER
in your .env file to "anthropic" or "gemini", and put your API key in
the matching variable. You only need ONE of these, whichever you prefer.

Get an Anthropic key at: https://console.anthropic.com/
Get a Gemini key at: https://aistudio.google.com/apikey
"""
import os
from dotenv import load_dotenv

load_dotenv()

AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _call_anthropic(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        msg = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return f"(AI request failed: {e})"

    # Find the actual text response - the model may return other block
    # types (e.g. thinking blocks) before or instead of a text block.
    for block in msg.content:
        if getattr(block, "type", None) == "text" and block.text.strip():
            return block.text
    return "(AI did not return a text response - try asking again, or shorten the question.)"


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(prompt)
    return response.text


def _call_ai(prompt: str) -> str:
    if AI_PROVIDER == "gemini":
        return _call_gemini(prompt)
    return _call_anthropic(prompt)


def draft_warning_email(study_name: str, checkpoint_pct: int, issues: list[str]) -> str:
    """Generates a plain-text email draft flagging data issues to a vendor/team."""
    if not issues:
        return "No issues detected at this checkpoint - no email needed."

    issues_text = "\n".join(f"- {i}" for i in issues)
    prompt = f"""You are a market research operations analyst. Write a clear,
professional email to send to a data vendor or field team, flagging data
quality issues found during fieldwork.

Study: {study_name}
Checkpoint: {checkpoint_pct}% complete

Issues detected:
{issues_text}

Write ONLY the email (with a short subject line, greeting, the issues
listed clearly, and a professional closing asking for action/response).
Do not add commentary outside the email. Do not invent any issues beyond
what is listed above."""
    return _call_ai(prompt)


def answer_question(study_name: str, checkpoint_pct: int, check_results: dict, question: str,
                     previous_wave: dict | None = None, data_map: str | None = None) -> str:
    """Lets the user ask a free-form question about the current QC results.
    Can answer topline questions, variable comparisons, or wave-over-wave
    comparisons if a previous wave's data is available."""
    topline = check_results.get("topline", {})
    total_respondents = check_results.get("total_respondents", "unknown")

    previous_block = "No previous wave data is available for comparison."
    if previous_wave:
        previous_block = f"""Wave {previous_wave['wave_number']} ({previous_wave['checkpoint_pct']}% complete, {previous_wave['total_respondents']} respondents):
{previous_wave['topline']}"""

    if data_map and data_map.strip():
        data_map_block = f"""
DATA MAP - what each variable/column code actually means:
{data_map.strip()}

You MUST use this to translate every column code you reference into its
plain-English label, formatted as "Label (code)" - e.g. "Awareness - Jeep
(V1r9)" - not the bare code alone. Match sub-item codes carefully (a
battery like V1 usually has one row per brand/attribute - V1r1, V1r2, ...
- each mapped to a different label in the data map; a "_T2B" or "T2Br"
suffix on a code is a Top-2-Box/summary version of that same underlying
item, so it shares that item's label). If the map doesn't cover a specific
code the person asked about, say so in ONE short sentence and still give
the best answer you can from the raw data - do not repeat the disclaimer
more than once or pad the answer with caveats.
"""
    else:
        data_map_block = """
No data map was provided for this study, so column codes cannot be
translated to plain-English labels. Mention this ONCE, briefly, then
answer using the raw column codes and the sample data - do not repeat
this caveat multiple times.
"""

    prompt = f"""You are a senior market research analyst answering a direct
question from a project lead, the way a sharp colleague would in a quick
Slack reply - lead with the actual answer, then briefly say where it came
from. Do not write a wall of caveats before getting to the number.
{data_map_block}
You have TOPLINE DATA (this wave's actual survey responses - each item may
include a category % distribution and/or mean/median/min/max), PREVIOUS
WAVE DATA (if available), and QC RESULTS (data quality flags).

How to answer:
- If the question asks about a specific metric (e.g. "what's the awareness
  of Jeep"): identify the matching column from the data map, then answer
  in this shape: state the number first in bold, then one line citing the
  source column and how it was computed, e.g.
  "**Jeep awareness (Top-2-Box): 81%** (1,605 of 1,981 respondents) — from
  column V1_T2Br9, the Top-2-Box flag for V1r9 (Awareness - Jeep) in your
  data map." If both a mean and a Top-2-Box/category % exist for the item,
  give the % first since that's what's usually meant by "awareness" or
  similar, and mention the mean as supporting detail only if useful.
- If asking for a topline summary / "story" of the data / overview:
  produce a structured mini-report with real numbers for every relevant
  question - percentages AND sample sizes (n), ranked leaderboards where
  relevant, called-out standouts (dominant leaders, close races). Use
  plain-English labels from the data map throughout, not raw codes alone.
- If comparing two or more variables: compute and describe that
  comparison from TOPLINE DATA with real numbers, using labels.
- If comparing this wave to a previous wave / trends over time: use
  PREVIOUS WAVE DATA alongside current TOPLINE DATA, state direction and
  size of change with real numbers.
- If about data quality/issues: use QC RESULTS.
If the answer genuinely isn't in the data provided, say so plainly in one
line rather than guessing. Never use vague language ("strong majority",
"many respondents") when an exact percentage or count is available.

Study: {study_name}
Current checkpoint: {checkpoint_pct}% complete
Current total respondents: {total_respondents}

CURRENT WAVE TOPLINE DATA:
{topline}

PREVIOUS WAVE DATA:
{previous_block}

QC RESULTS (data quality flags only):
{check_results.get("issues", [])}

Question: {question}

Answer directly and concisely, the way you'd actually reply to a
colleague who asked you this in person - lead with the number/answer,
then a brief source citation. Avoid long preambles."""
    return _call_ai(prompt)


def generate_insight_summary(study_name: str, checkpoint_pct: int, check_results: dict,
                              previous_wave: dict | None = None, data_map: str | None = None) -> str:
    """Generates a full executive-style topline report from the actual survey data."""
    topline = check_results.get("topline", {})
    total_respondents = check_results.get("total_respondents", "unknown")

    if data_map and data_map.strip():
        data_map_block = f"""
DATA MAP - what each variable/column code actually means:
{data_map.strip()}

You MUST use this to translate every column code into its plain-English
label, formatted as "Label (code)" - e.g. "Awareness - Jeep (V1r9)" - the
first time each code appears, then you can use the label alone after that.
Match sub-item codes carefully (a battery like V1 usually has one row per
brand/attribute - V1r1, V1r2, ... - each mapped to a different label in
the data map; a "_T2B"/"T2Br" suffix on a code is a Top-2-Box/summary
version of that same underlying item and shares its label). If the map
doesn't cover a specific code, note that ONCE briefly in a single line at
the point it's first needed - do not repeat the caveat in every section.
"""
    else:
        data_map_block = """
No data map was provided for this study, so column codes cannot be
translated to plain-English labels. Note this ONCE, briefly, near the top
of the report, then proceed to report on the raw codes and their data -
do not repeat this caveat in every section.
"""

    previous_block = ""
    if previous_wave:
        previous_block = f"""
PREVIOUS WAVE {previous_wave['wave_number']} ({previous_wave['checkpoint_pct']}% complete, {previous_wave['total_respondents']} respondents):
{previous_wave['topline']}

Include a wave-over-wave comparison section if anything notable changed
between waves (call out specific point/percentage moves)."""

    prompt = f"""You are a senior market research analyst producing a
Topline Executive Summary for a client, in the style of a professional
research report. Use ONLY the TOPLINE DATA below - do not invent numbers,
brands, or findings that aren't in the data. Each item in TOPLINE DATA may
include a category % distribution (per response code) and/or mean/median/
min/max - use whichever is present; many coded items now include both.

Structure the report with markdown headers, similar to this shape (adapt
section names and content to what's actually in the data - skip sections
that don't apply, e.g. skip brand leaderboards if there are no
brand-preference-style questions):

# Topline Summary — {study_name}

## Survey Overview & Sample Profile
State the total sample size, and break down every demographic-style
variable found in the data (gender, age, region, income, etc.) with exact
percentages AND respondent counts, e.g. "54.2% Female (n=1,073)". Use the
data map to give the real category label, not just the raw code, e.g.
"Region: West (hidRegion=1) 28.4% (n=563)" rather than just "hidRegion:
mean 2.61". Compute n from the % and total respondents if only % is given.

## Key Findings
For every substantive (non-demographic) question, report the actual
numbers - percentages AND counts - not vague language, using plain-English
labels from the data map. Where a question has many response options
(e.g. a brand-preference, awareness, or category question), rank the
options and call out the leader(s), especially where one option has a
clear majority or where the race is close between top options.

## Strategic Highlights
Pull out the most notable patterns: dominant leaders (large gaps between
#1 and #2), close competitive races, anything a client should act on.
{previous_block}
{data_map_block}
Study: {study_name}
Checkpoint: {checkpoint_pct}% complete
Total respondents: {total_respondents}

TOPLINE DATA (actual responses per question - category % distribution
and/or mean/median/min/max, with sample sizes):
{topline}

Data quality flags (mention briefly only if material to trust in the findings):
{check_results.get("issues", [])}

Write the full report now. Use real numbers and plain-English labels
throughout - every claim should be backed by a specific percentage or
count from the data above, with the underlying code shown once alongside
its label.
"""
    return _call_ai(prompt)
