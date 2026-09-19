from flask import Flask, request, render_template_string
from werkzeug.utils import secure_filename
from pypdf import PdfReader
import re
from datetime import datetime

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
ALLOWED = {"txt", "pdf"}


# =========================================================
# TEXT / DOCUMENT
# =========================================================

def clean_line(x):
    return re.sub(r"\s+", " ", x or "").strip(" -•\t")


def first_lines(text, n=12):
    return [clean_line(x) for x in text.splitlines() if clean_line(x)][:n]


def detect_title(text):
    lines = first_lines(text, 15)
    return lines[0] if lines else "Untitled Document"


def extract_text(file):
    name = secure_filename(file.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext not in ALLOWED:
        raise ValueError("Unsupported file type. Upload a PDF or TXT file.")

    if ext == "txt":
        return file.read().decode("utf-8", errors="ignore").strip()

    reader = PdfReader(file)
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    if not text:
        raise ValueError(
            "This PDF does not contain readable text. "
            "Try a text-based PDF or TXT document."
        )

    return text


# =========================================================
# DOCUMENT INTELLIGENCE
# =========================================================

TYPE_RULES = [
    ("Job Opportunity", ["job", "recruitment", "vacancy", "position", "employee", "career"]),
    ("Internship / Training", ["internship", "intern", "training program", "trainee"]),
    ("Scholarship / Financial Aid", ["scholarship", "financial assistance", "student aid", "stipend"]),
    ("Grant / Funding Opportunity", ["grant", "funding opportunity", "funding", "financial support"]),
    ("Admission Notice", ["admission", "admissions", "enrollment", "college admission"]),
    ("Fellowship", ["fellowship", "fellow"]),
    ("Competition / Challenge", ["competition", "challenge", "hackathon", "contest"]),
    ("Government Scheme / Program", ["government scheme", "scheme", "government program", "beneficiary"]),
    ("Tender / RFP", ["tender", "request for proposal", "rfp", "bid submission"]),
    ("Policy / Rules", ["policy", "rules", "regulations", "terms and conditions"]),
]


def detect_document_type(text):
    low = text.lower()
    scores = []

    for name, words in TYPE_RULES:
        score = sum(1 for word in words if word in low)
        if score:
            scores.append((score, name))

    if not scores:
        return "General Notice"

    return sorted(scores, reverse=True)[0][1]


def detect_purpose(text, doc_type):
    purposes = {
        "Job Opportunity":
            "Identify whether the applicant matches the role requirements and what to do next.",
        "Internship / Training":
            "Check eligibility, requirements, documents and next steps for the opportunity.",
        "Scholarship / Financial Aid":
            "Determine whether the applicant meets the scholarship conditions and application requirements.",
        "Grant / Funding Opportunity":
            "Check whether the applicant or organization matches the funding conditions.",
        "Admission Notice":
            "Check admission requirements, documents, deadlines and application steps.",
        "Fellowship":
            "Evaluate fellowship requirements and identify the actions needed to apply.",
        "Competition / Challenge":
            "Identify participation requirements, deadlines and submission actions.",
        "Government Scheme / Program":
            "Understand applicable conditions, documents and actions required to access the program.",
        "Tender / RFP":
            "Identify submission requirements, conditions, documents and important deadlines.",
        "Policy / Rules":
            "Understand the important rules, conditions and obligations in the document."
    }

    return purposes.get(
        doc_type,
        "Convert the document into structured requirements, conditions and actionable next steps."
    )


def section_items(text, headings):
    lines = text.splitlines()
    active = False
    result = []

    for raw in lines:
        line = clean_line(raw)
        if not line:
            continue

        low = line.lower().rstrip(":")

        if any(low == h or low.startswith(h + " ") for h in headings):
            active = True
            continue

        if active:
            if line.isupper() and len(line) < 80:
                break

            if re.match(r"^\d+[\.\)]\s+", line) or line.startswith(("-", "•", "*")):
                item = re.sub(r"^\d+[\.\)]\s*", "", line)
                item = item.lstrip("-•* ").strip()
                if item:
                    result.append(item)

    return result[:12]


def extract_deadline(text):
    patterns = [
        r"(?:application|submission|registration|last|final).*?"
        r"(?:deadline|date|before|by|closes?).{0,40}"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",

        r"(?:deadline|last date|closing date|submission date)"
        r".{0,50}"
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",

        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return match.group(1)

    return "Not detected"


def parse_date(value):
    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    return None


def deadline_info(deadline):
    if deadline == "Not detected":
        return {
            "label": "Not detected",
            "class": "neutral",
            "message": "No clear deadline was detected."
        }

    date = parse_date(deadline)

    if not date:
        return {
            "label": deadline,
            "class": "neutral",
            "message": "Review the original document to confirm the deadline."
        }

    days = (date - datetime.now().date()).days

    if days < 0:
        return {
            "label": deadline,
            "class": "danger",
            "message": f"Deadline passed {abs(days)} day(s) ago."
        }

    if days == 0:
        return {
            "label": deadline,
            "class": "danger",
            "message": "Deadline is today. Act immediately."
        }

    if days <= 3:
        return {
            "label": deadline,
            "class": "danger",
            "message": f"{days} day(s) remaining. High urgency."
        }

    if days <= 7:
        return {
            "label": deadline,
            "class": "warning",
            "message": f"{days} day(s) remaining. Prepare soon."
        }

    return {
        "label": deadline,
        "class": "safe",
        "message": f"{days} day(s) remaining."
    }


# =========================================================
# REQUIREMENT EXTRACTION
# =========================================================

def add_requirement(items, category, text, key, confidence="High"):
    if not any(x["key"] == key for x in items):
        items.append({
            "category": category,
            "text": text,
            "key": key,
            "confidence": confidence
        })


def extract_requirements(text):
    low = text.lower()
    req = []

    match = re.search(
        r"\b(?:minimum|min\.?)\s+(?:academic\s+)?"
        r"(?:score|percentage|marks?)\s*(?:of)?\s*(\d+)",
        low
    )

    if match:
        add_requirement(
            req,
            "Academic",
            f"Minimum academic score: {match.group(1)}%",
            "score"
        )

    match = re.search(
        r"(?:family|annual).*?(?:income).*?"
        r"(?:below|less than|under).*?"
        r"(?:inr|rs\.?|₹)?\s*([\d,]+)",
        low
    )

    if match:
        value = int(match.group(1).replace(",", ""))
        add_requirement(
            req,
            "Financial",
            f"Annual/family income must be below INR {value:,}",
            "income"
        )

    match = re.search(
        r"(?:between|age).*?(\d{2}).*?"
        r"(?:and|-).*?(\d{2})\s*(?:years|yrs)?",
        low
    )

    if match:
        add_requirement(
            req,
            "Age",
            f"Age must be between {match.group(1)} and {match.group(2)} years",
            "age"
        )

    match = re.search(
        r"(?:at least|minimum|min\.?)\s*(\d+)\s*"
        r"(?:years?|yrs?)\s*(?:of)?\s*"
        r"(?:professional\s+)?(?:work\s+)?experience",
        low
    )

    if match:
        add_requirement(
            req,
            "Experience",
            f"Minimum experience: {match.group(1)} year(s)",
            "experience"
        )

    if re.search(r"indian citizen|citizen of india|indian nationality", low):
        add_requirement(
            req,
            "Citizenship",
            "Applicant must be an Indian citizen",
            "citizenship"
        )

    programs = re.findall(
        r"computer science|information technology|engineering|"
        r"electronics|technology|business|management|finance|"
        r"commerce|science|arts",
        low
    )

    if programs:
        unique = list(dict.fromkeys(programs))
        add_requirement(
            req,
            "Education / Field",
            "Relevant field/program: " +
            ", ".join(x.title() for x in unique[:5]),
            "program"
        )

    years = re.findall(r"\b20\d{2}\b", text)

    if re.search(r"graduation|graduate|passing year|graduating", low) and years:
        years = [int(y) for y in years if int(y) >= 2025]

        if years:
            add_requirement(
                req,
                "Education",
                f"Graduation year requirement detected around {max(years)}",
                "graduation_year",
                "Medium"
            )

    skills = []

    for skill in [
        "python", "java", "javascript", "sql", "react",
        "machine learning", "data analysis", "git",
        "rest api", "cloud", "c++", "c"
    ]:
        if skill in low:
            skills.append(skill)

    if skills:
        add_requirement(
            req,
            "Skills",
            "Relevant skills: " + ", ".join(skills),
            "skills",
            "Medium"
        )

    locations = re.findall(
        r"\b(?:Hyderabad|Bengaluru|Bangalore|Mumbai|Delhi|"
        r"Chennai|Pune|Kolkata|Gurugram|Noida|India)\b",
        text,
        re.I
    )

    if locations and re.search(r"work|location|located|based|from", low):
        unique = list(dict.fromkeys(x.title() for x in locations))
        add_requirement(
            req,
            "Location",
            "Location condition: " + ", ".join(unique[:4]),
            "location",
            "Medium"
        )

    if re.search(r"currently enrolled|currently pursuing|enrolled in", low):
        add_requirement(
            req,
            "Enrollment",
            "Applicant must currently be enrolled/pursuing the required program",
            "enrollment",
            "Medium"
        )

    return req


# =========================================================
# MATCHING
# =========================================================

def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def evaluate(req, profile):
    key = req["key"]
    value = profile.get(key, "").strip()

    if not value:
        return {
            "status": "VERIFY",
            "reason": "Information not provided.",
            "evidence": req["text"]
        }

    if key == "score":
        try:
            user_score = float(value)
            threshold = int(re.search(r"\d+", req["text"]).group())

            if user_score >= threshold:
                return {
                    "status": "MEETS",
                    "reason": f"{user_score}% meets the minimum.",
                    "evidence": req["text"]
                }

            return {
                "status": "DOES NOT MEET",
                "reason": f"{user_score}% is below the minimum.",
                "evidence": req["text"]
            }
        except ValueError:
            return {
                "status": "VERIFY",
                "reason": "Enter a valid numeric score.",
                "evidence": req["text"]
            }

    if key == "income":
        try:
            income = float(value.replace(",", ""))
            threshold = int(
                re.search(r"[\d,]+", req["text"]).group().replace(",", "")
            )

            if income < threshold:
                return {
                    "status": "MEETS",
                    "reason": "Income is within the stated limit.",
                    "evidence": req["text"]
                }

            return {
                "status": "DOES NOT MEET",
                "reason": "Income is above the stated limit.",
                "evidence": req["text"]
            }
        except ValueError:
            return {
                "status": "VERIFY",
                "reason": "Enter a valid income value.",
                "evidence": req["text"]
            }

    if key == "age":
        nums = [int(x) for x in re.findall(r"\d+", req["text"])]

        try:
            age = int(value)

            if len(nums) >= 2 and nums[0] <= age <= nums[1]:
                return {
                    "status": "MEETS",
                    "reason": "Age is within the stated range.",
                    "evidence": req["text"]
                }

            return {
                "status": "DOES NOT MEET",
                "reason": "Age is outside the stated range.",
                "evidence": req["text"]
            }
        except ValueError:
            return {
                "status": "VERIFY",
                "reason": "Enter a valid age.",
                "evidence": req["text"]
            }

    if key == "experience":
        try:
            years = float(value)
            required = float(re.search(r"\d+", req["text"]).group())

            if years >= required:
                return {
                    "status": "MEETS",
                    "reason": "Experience meets the minimum.",
                    "evidence": req["text"]
                }

            return {
                "status": "DOES NOT MEET",
                "reason": "Experience is below the minimum.",
                "evidence": req["text"]
            }
        except ValueError:
            return {
                "status": "VERIFY",
                "reason": "Enter valid experience.",
                "evidence": req["text"]
            }

    if key == "citizenship":
        v = normalize(value)

        if v in ["yes", "y", "indian", "indian citizen", "india"]:
            return {
                "status": "MEETS",
                "reason": "Citizenship requirement satisfied.",
                "evidence": req["text"]
            }

        if v in ["no", "n"]:
            return {
                "status": "DOES NOT MEET",
                "reason": "Citizenship requirement is not satisfied.",
                "evidence": req["text"]
            }

        return {
            "status": "VERIFY",
            "reason": "Citizenship needs verification.",
            "evidence": req["text"]
        }

    if key == "program":
        user = normalize(value)

        terms = re.findall(
            r"computer science|information technology|engineering|"
            r"electronics|technology|business|management|finance|"
            r"commerce|science|arts",
            req["text"].lower()
        )

        if any(term in user for term in terms):
            return {
                "status": "MEETS",
                "reason": "Provided field appears relevant.",
                "evidence": req["text"]
            }

        return {
            "status": "VERIFY",
            "reason": "Field relevance needs verification.",
            "evidence": req["text"]
        }

    if key == "skills":
        user = normalize(value)

        needed = re.findall(
            r"python|java|javascript|sql|react|machine learning|"
            r"data analysis|git|rest api|cloud|c\+\+|c",
            req["text"].lower()
        )

        matched = [x for x in needed if normalize(x) in user]

        if matched:
            return {
                "status": "MEETS",
                "reason": "Relevant skill detected.",
                "evidence": req["text"]
            }

        return {
            "status": "VERIFY",
            "reason": "Skill match needs closer verification.",
            "evidence": req["text"]
        }

    if key == "location":
        user = normalize(value)

        places = re.findall(
            r"hyderabad|bengaluru|bangalore|mumbai|delhi|"
            r"chennai|pune|kolkata|gurugram|noida|india",
            req["text"].lower()
        )

        if any(normalize(x) in user for x in places):
            return {
                "status": "MEETS",
                "reason": "Location appears to match.",
                "evidence": req["text"]
            }

        return {
            "status": "VERIFY",
            "reason": "Location compatibility needs verification.",
            "evidence": req["text"]
        }

    if key == "graduation_year":
        try:
            year = int(value)
            required = max(
                int(x) for x in re.findall(r"20\d{2}", req["text"])
            )

            if year >= required:
                return {
                    "status": "MEETS",
                    "reason": "Graduation year satisfies the condition.",
                    "evidence": req["text"]
                }

            return {
                "status": "DOES NOT MEET",
                "reason": "Graduation year does not satisfy the condition.",
                "evidence": req["text"]
            }
        except ValueError:
            return {
                "status": "VERIFY",
                "reason": "Enter a valid graduation year.",
                "evidence": req["text"]
            }

    if key == "enrollment":
        v = normalize(value)

        if v in ["yes", "y", "currently enrolled", "student"]:
            return {
                "status": "MEETS",
                "reason": "Enrollment requirement satisfied.",
                "evidence": req["text"]
            }

        if v in ["no", "n"]:
            return {
                "status": "DOES NOT MEET",
                "reason": "Enrollment requirement is not satisfied.",
                "evidence": req["text"]
            }

        return {
            "status": "VERIFY",
            "reason": "Enrollment needs verification.",
            "evidence": req["text"]
        }

    return {
        "status": "VERIFY",
        "reason": "This condition requires manual verification.",
        "evidence": req["text"]
    }


# =========================================================
# ACTIONS / WARNINGS
# =========================================================

def build_actions(results, deadline, docs, status):
    actions = []

    if any(x["result"]["status"] == "DOES NOT MEET" for x in results):
        actions.append(
            "Review the requirements marked as DOES NOT MEET before proceeding."
        )

    if any(x["result"]["status"] == "VERIFY" for x in results):
        actions.append(
            "Verify the requirements marked as VERIFY using the original document."
        )

    if docs:
        actions.append(
            "Collect and check all required documents before submission."
        )

    if deadline != "Not detected":
        actions.append(f"Track the stated deadline: {deadline}.")

    if status == "MEETS":
        actions.append("Proceed with the application or next process step.")
    elif status == "VERIFY":
        actions.append(
            "Resolve the verification items before making a final decision."
        )
    else:
        actions.append(
            "Review the unmet mandatory conditions before proceeding."
        )

    return list(dict.fromkeys(actions))


def extract_warnings(text):
    low = text.lower()
    warnings = []

    if "incomplete" in low:
        warnings.append("Incomplete submissions may not be considered.")

    if "accurate" in low or "accuracy" in low:
        warnings.append("Verify that submitted information is accurate.")

    if "mandatory" in low:
        warnings.append("Some requirements may be mandatory.")

    if "rejected" in low or "reject" in low:
        warnings.append(
            "Failure to satisfy stated conditions may lead to rejection."
        )

    if "non-refundable" in low:
        warnings.append(
            "Look for non-refundable payment conditions."
        )

    return list(dict.fromkeys(warnings))[:6]


# =========================================================
# UI
# =========================================================

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:#f5f7fb;color:#172033}
.top{background:#101827;color:#fff;padding:18px 5%;display:flex;justify-content:space-between;align-items:center}
.logo{font-size:23px;font-weight:800;letter-spacing:-.5px}
.logo span{color:#72e0b8}
.tag{font-size:12px;color:#aab5c7}
.wrap{max-width:1180px;margin:auto;padding:30px 20px}
.hero{padding:30px 0 22px}
.hero h1{font-size:38px;line-height:1.05;margin:0 0 10px;letter-spacing:-1.5px}
.hero p{color:#657084;max-width:720px;font-size:16px;line-height:1.6;margin:0}
.card{background:#fff;border:1px solid #e6eaf0;border-radius:18px;padding:22px;box-shadow:0 8px 30px rgba(15,23,42,.05)}
.upload{border:2px dashed #cdd5e1;text-align:center;padding:48px 20px;margin-top:18px}
.upload h2{margin:0 0 8px}
.upload p{color:#687386}
input[type=file]{margin:18px 0;padding:12px;width:100%;max-width:520px;border:1px solid #d9dee7;border-radius:10px;background:#fafbfc}
button{border:0;border-radius:10px;padding:13px 20px;background:#101827;color:white;font-weight:700;cursor:pointer}
button:hover{opacity:.92}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}
.stat{background:#fff;border:1px solid #e6eaf0;border-radius:15px;padding:17px}
.stat small{display:block;color:#7a8495;font-size:11px;text-transform:uppercase;font-weight:800;letter-spacing:.7px}
.stat strong{display:block;font-size:20px;margin-top:7px}
.section{margin-top:18px}
.section h2{font-size:19px;margin:0 0 13px}
.decision{padding:25px;border-radius:18px;color:#fff;margin-top:18px}
.decision h2{margin:0 0 7px;font-size:26px}
.decision p{margin:0;opacity:.9}
.meets{background:linear-gradient(135deg,#087f5b,#16a477)}
.fail{background:linear-gradient(135deg,#a61b2b,#d94757)}
.verify{background:linear-gradient(135deg,#9a6500,#d38a12)}
.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.req{background:#fff;border:1px solid #e6eaf0;border-radius:15px;padding:18px}
.req-top{display:flex;justify-content:space-between;gap:10px;align-items:center}
.badge{font-size:11px;font-weight:800;border-radius:999px;padding:5px 9px;white-space:nowrap}
.b-meets{background:#dcfce7;color:#166534}
.b-fail{background:#fee2e2;color:#991b1b}
.b-verify{background:#fef3c7;color:#92400e}
.b-confidence{background:#eef2ff;color:#4338ca}
.req h3{margin:11px 0 7px;font-size:16px}
.req p{margin:5px 0;color:#596579;font-size:13px;line-height:1.5}
.evidence{margin-top:11px;background:#f7f8fb;border-radius:10px;padding:10px;font-size:12px;color:#687386}
.action{display:flex;gap:11px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #edf0f4}
.action:last-child{border:0}
.tick{width:25px;height:25px;border-radius:50%;background:#101827;color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800;flex:none}
.deadline{padding:18px;border-radius:14px;border:1px solid #e4e8ef}
.deadline.safe{background:#ecfdf5;border-color:#b7ead6}
.deadline.warning{background:#fff8e7;border-color:#f0d58a}
.deadline.danger{background:#fff0f1;border-color:#f2b6bd}
.deadline strong{font-size:18px}
.list{margin:0;padding-left:19px;color:#566174;line-height:1.8}
.error{background:#fff0f1;border:1px solid #f1b8c0;color:#a51c2d;padding:15px;border-radius:12px;margin-bottom:18px;font-weight:600}
form.profile{display:grid;grid-template-columns:repeat(2,1fr);gap:15px}
.field label{display:block;font-size:12px;font-weight:800;margin-bottom:6px;color:#596579}
.field input{width:100%;padding:12px;border:1px solid #dce1e9;border-radius:9px}
.full{grid-column:1/-1}
.info{padding:14px;background:#f1f5ff;border:1px solid #d9e2ff;border-radius:12px;color:#475569;font-size:13px;line-height:1.5}
.footer{text-align:center;color:#8a94a5;padding:30px;font-size:12px}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}.cards{grid-template-columns:1fr}form.profile{grid-template-columns:1fr}.full{grid-column:auto}.hero h1{font-size:31px}}
@media(max-width:500px){.grid{grid-template-columns:1fr}.top{padding:15px 20px}.tag{display:none}}
"""

BASE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}} | ActionLens</title>
<style>{{css}}</style>
</head>
<body>
<header class="top">
<div class="logo">Action<span>Lens</span></div>
<div class="tag">Document → Decision → Action</div>
</header>
<main class="wrap">{{body|safe}}</main>
<footer class="footer">
ActionLens · Turn complex documents into clear next steps
</footer>
</body>
</html>
"""


def render_page(title, body):
    return render_template_string(
        BASE,
        title=title,
        body=body,
        css=CSS
    )


# =========================================================
# ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def home():
    body = """
    <section class="hero">
      <h1>Turn any document into a clear decision.</h1>
      <p>
        ActionLens reads complex notices, opportunities, schemes and rules,
        identifies important conditions, and turns them into personalized
        next steps.
      </p>
    </section>

    <section class="card upload">
      <h2>Upload a document</h2>
      <p>Supports text-based PDF and TXT files.</p>

      <form method="post" action="/analyze" enctype="multipart/form-data">
        <input type="file"
               name="document"
               accept=".pdf,.txt,application/pdf,text/plain"
               required>
        <br>
        <button type="submit">Analyze Document →</button>
      </form>
    </section>

    <section class="grid">
      <div class="stat"><small>Input</small><strong>PDF / TXT</strong></div>
      <div class="stat"><small>Analysis</small><strong>Requirements</strong></div>
      <div class="stat"><small>Decision</small><strong>Match</strong></div>
      <div class="stat"><small>Output</small><strong>Next Steps</strong></div>
    </section>
    """

    return render_page("Upload", body)


@app.route("/analyze", methods=["POST"])
def analyze():

    # -----------------------------------------------------
    # STAGE 2: USER ANSWERS
    # -----------------------------------------------------
    # IMPORTANT:
    # This check MUST happen before checking request.files.
    # The second form intentionally contains no uploaded file.

    if request.form.get("stage") == "answers":
        raw_text = request.form.get("raw_text", "")
        title = request.form.get("title", "Document")

        if not raw_text.strip():
            return render_page(
                "Session Error",
                '<div class="error">'
                'The document session expired. Please upload the document again.'
                '</div>'
                '<a href="/"><button>← Upload Again</button></a>'
            )

        return process_analysis(request, raw_text, title)

    # -----------------------------------------------------
    # STAGE 1: FILE UPLOAD
    # -----------------------------------------------------

    file = request.files.get("document")

    if not file or not file.filename:
        return render_page(
            "Upload Error",
            '<div class="error">'
            'Please select a PDF or TXT document.'
            '</div>'
            '<a href="/"><button>← Try Again</button></a>'
        )

    try:
        text = extract_text(file)
    except Exception as e:
        return render_page(
            "Upload Error",
            f'<div class="error">{str(e)}</div>'
            '<a href="/"><button>← Try Again</button></a>'
        )

    if not text.strip():
        return render_page(
            "Upload Error",
            '<div class="error">'
            'The uploaded document appears to be empty.'
            '</div>'
            '<a href="/"><button>← Try Again</button></a>'
        )

    title = detect_title(text)
    doc_type = detect_document_type(text)
    purpose = detect_purpose(text, doc_type)
    requirements = extract_requirements(text)

    labels = {
        "score": "Academic score (%)",
        "income": "Annual / family income (INR)",
        "age": "Age",
        "experience": "Professional experience (years)",
        "citizenship": "Indian citizen? (yes/no)",
        "program": "Degree / program / field",
        "skills": "Skills you have",
        "location": "Preferred/current location",
        "graduation_year": "Graduation year",
        "enrollment": "Currently enrolled/pursuing? (yes/no)"
    }

    fields = []

    for req in requirements:
        if req["key"] not in [x["key"] for x in fields]:
            fields.append(req)

    if not fields:
        fields = [{
            "key": "program",
            "category": "General",
            "text": "Provide the relevant field or background.",
            "confidence": "Medium"
        }]

    fields_html = ""

    for req in fields:
        key = req["key"]

        fields_html += f"""
        <div class="field">
          <label>{labels.get(key, key.replace("_", " ").title())}</label>
          <input name="{key}" required>
        </div>
        """

    # Escape document content for HTML attribute storage.
    safe_text = (
        text.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

    safe_title = (
        title.replace("&", "&amp;")
             .replace('"', "&quot;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
    )

    profile_form = f"""
    <section class="hero">
      <h1>Personalize the analysis.</h1>
      <p>{purpose}</p>
    </section>

    <div class="info">
      ActionLens detected
      <strong>{len(requirements)}</strong>
      relevant requirement(s) from this document.
      Answer the questions below so ActionLens can compare
      your information with the document.
    </div>

    <section class="card section">
      <form class="profile" method="post" action="/analyze">

        <input type="hidden" name="stage" value="answers">
        <input type="hidden" name="raw_text" value="{safe_text}">
        <input type="hidden" name="title" value="{safe_title}">

        {fields_html}

        <div class="full">
          <button type="submit">
            Generate My Decision →
          </button>
        </div>

      </form>
    </section>
    """

    return render_page("Personalize", profile_form)


# =========================================================
# FINAL ANALYSIS
# =========================================================

def process_analysis(req, text, title):

    doc_type = detect_document_type(text)
    purpose = detect_purpose(text, doc_type)
    requirements = extract_requirements(text)

    deadline = extract_deadline(text)
    deadline_data = deadline_info(deadline)

    docs = section_items(
        text,
        [
            "required documents",
            "documents required",
            "documents",
            "required proofs"
        ]
    )

    process = section_items(
        text,
        [
            "application process",
            "process",
            "how to apply",
            "procedure"
        ]
    )

    benefits = section_items(
        text,
        [
            "benefits",
            "outcomes",
            "selected students will receive",
            "what you get"
        ]
    )

    warnings = extract_warnings(text)

    profile = {
        key: req.form.get(key, "").strip()
        for key in [
            "score",
            "income",
            "age",
            "experience",
            "citizenship",
            "program",
            "skills",
            "location",
            "graduation_year",
            "enrollment"
        ]
    }

    results = []

    for requirement in requirements:
        results.append({
            "requirement": requirement,
            "result": evaluate(requirement, profile)
        })

    failed = sum(
        1 for x in results
        if x["result"]["status"] == "DOES NOT MEET"
    )

    verify = sum(
        1 for x in results
        if x["result"]["status"] == "VERIFY"
    )

    meets = sum(
        1 for x in results
        if x["result"]["status"] == "MEETS"
    )

    if failed:
        status = "DOES NOT MEET"
    elif verify:
        status = "VERIFY"
    elif meets:
        status = "MEETS"
    else:
        status = "VERIFY"

    status_map = {
        "MEETS": (
            "meets",
            "Requirements currently match",
            "The information provided matches the detected requirements."
        ),
        "DOES NOT MEET": (
            "fail",
            "Does not meet all requirements",
            "At least one detected requirement is not satisfied."
        ),
        "VERIFY": (
            "verify",
            "Verification required",
            "Some conditions cannot be confidently confirmed."
        )
    }

    status_class, status_title, status_message = status_map[status]

    actions = build_actions(
        results,
        deadline,
        docs,
        status
    )

    cards = ""

    for item in results:

        r = item["requirement"]
        result = item["result"]

        if result["status"] == "MEETS":
            badge = "b-meets"
        elif result["status"] == "DOES NOT MEET":
            badge = "b-fail"
        else:
            badge = "b-verify"

        cards += f"""
        <article class="req">

          <div class="req-top">
            <span class="badge {badge}">
              {result["status"]}
            </span>

            <span class="badge b-confidence">
              {r["confidence"]} confidence
            </span>
          </div>

          <h3>{r["category"]}</h3>

          <p>
            <strong>{r["text"]}</strong>
          </p>

          <p>{result["reason"]}</p>

          <div class="evidence">
            <strong>Document evidence:</strong><br>
            {result["evidence"]}
          </div>

        </article>
        """

    action_html = ""

    for i, action in enumerate(actions, 1):
        action_html += f"""
        <div class="action">
          <div class="tick">{i}</div>
          <div>{action}</div>
        </div>
        """

    docs_html = (
        "".join(f"<li>{x}</li>" for x in docs)
        if docs
        else "<li>No required-document section detected.</li>"
    )

    process_html = (
        "".join(f"<li>{x}</li>" for x in process)
        if process
        else "<li>No explicit application process detected.</li>"
    )

    benefits_html = (
        "".join(f"<li>{x}</li>" for x in benefits)
        if benefits
        else "<li>No explicit benefits/outcomes detected.</li>"
    )

    warning_html = (
        "".join(f"<li>{x}</li>" for x in warnings)
        if warnings
        else "<li>No major warning phrase detected.</li>"
    )

    body = f"""
    <section class="hero">
      <h1>{title}</h1>
      <p>{purpose}</p>
    </section>

    <div class="grid">

      <div class="stat">
        <small>Document Type</small>
        <strong>{doc_type}</strong>
      </div>

      <div class="stat">
        <small>Requirements</small>
        <strong>{len(requirements)}</strong>
      </div>

      <div class="stat">
        <small>Documents</small>
        <strong>{len(docs)}</strong>
      </div>

      <div class="stat">
        <small>Deadline</small>
        <strong>{deadline}</strong>
      </div>

    </div>

    <section class="decision {status_class}">
      <h2>{status_title}</h2>
      <p>{status_message}</p>
    </section>

    <div class="grid">

      <div class="stat">
        <small>Requirements Met</small>
        <strong>{meets}</strong>
      </div>

      <div class="stat">
        <small>Not Met</small>
        <strong>{failed}</strong>
      </div>

      <div class="stat">
        <small>Need Verification</small>
        <strong>{verify}</strong>
      </div>

      <div class="stat">
        <small>Type</small>
        <strong>{doc_type}</strong>
      </div>

    </div>

    <section class="section">
      <h2>Requirement Intelligence</h2>

      <div class="cards">
        {
            cards if cards
            else
            '<div class="card">'
            'No specific requirements were detected.'
            '</div>'
        }
      </div>
    </section>

    <section class="section">

      <h2>What Should You Do Next?</h2>

      <div class="card">
        {action_html}
      </div>

    </section>

    <section class="section">

      <h2>Deadline Intelligence</h2>

      <div class="deadline {deadline_data["class"]}">
        <strong>{deadline_data["label"]}</strong>
        <p>{deadline_data["message"]}</p>
      </div>

    </section>

    <section class="section">

      <h2>Required Documents</h2>

      <div class="card">
        <ul class="list">
          {docs_html}
        </ul>
      </div>

    </section>

    <section class="section">

      <h2>Application / Process Steps</h2>

      <div class="card">
        <ol class="list">
          {process_html}
        </ol>
      </div>

    </section>

    <section class="section">

      <h2>Benefits / Outcomes</h2>

      <div class="card">
        <ul class="list">
          {benefits_html}
        </ul>
      </div>

    </section>

    <section class="section">

      <h2>Important Conditions & Warnings</h2>

      <div class="card">
        <ul class="list">
          {warning_html}
        </ul>
      </div>

    </section>

    <section class="section" style="text-align:center">
      <a href="/">
        <button>Analyze Another Document</button>
      </a>
    </section>
    """

    return render_page("Decision", body)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)