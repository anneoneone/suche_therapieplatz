import json
import re

# Ask for user's personal information
def get_user_info():
    print("Bitte geben Sie Ihre persönlichen Daten ein.")
    name = input("Name: ")
    email = input("E-Mail: ")
    telefon = input("Telefonnummer: ")
    address = input("Adresse: ")
    vermittlungscode = input("Vermittlungscode: ")
    return {
        "name": name,
        "email": email,
        "telefon": telefon,
        "address": address,
        "vermittlungscode": vermittlungscode
    }

# Get user's personal information
user_info = get_user_info()

def parse_psychotherapists(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    data = []
    current_entry = {}
    current_section = None

    for line in lines:
        line = line.strip()
        
        if not line:
            continue

        if line.startswith("Psychologische Psychotherapeutin") or line.startswith("Psychologischer Psychotherapeut"):
            if current_entry:
                data.append(current_entry)
                current_entry = {}
            current_section = "name"
            continue

        if current_section == "name":
            current_entry["name"] = line
            current_section = "address"
            continue

        if current_section == "address":
            if "address" not in current_entry:
                current_entry["address"] = line
            else:
                current_entry["address"] += ", " + line
            if len(current_entry["address"].split(", ")) == 2:
                current_section = None
            continue

        if line.startswith("Tel.:"):
            current_entry["telefon"] = line.split("Tel.:")[1].strip()
            continue

        if line.startswith("E-Mail:"):
            current_entry["email"] = line.split("E-Mail:")[1].strip()
            continue

        if line == "Psychotherapie":
            current_section = "therapieform"
            current_entry["therapieform"] = []
            continue

        if current_section == "therapieform":
            if line.startswith("Sprechzeiten"):
                current_section = "sprechzeiten"
                current_entry["sprechzeiten"] = []
                continue
            current_entry["therapieform"].append(line)
            continue

        if current_section == "sprechzeiten":
            if line.startswith("Psychologische Psychotherapeutin") or line.startswith("Psychologischer Psychotherapeut"):
                data.append(current_entry)
                current_entry = {}
                current_section = "name"
                continue
            current_entry["sprechzeiten"].append(line)
            continue

    if current_entry:
        data.append(current_entry)

    return data

def create_markdown_table(data, markdown_file_path):
    with open(markdown_file_path, 'w', encoding='utf-8') as md_file:
        # Write the header
        md_file.write("| Name | Adresse | Telefon | E-Mail | Therapieform | Sprechzeiten | Kontaktiert | Datum | Status |\n")
        md_file.write("|------|---------|---------|--------|--------------|--------------|-------------|-------|--------|\n")
        
        # Write the data rows
        for entry in data:
            name = entry.get("name", "")
            address = entry.get("address", "")
            telefon = entry.get("telefon", "")
            email = entry.get("email", "")
            therapieform = "<br>".join(entry.get("therapieform", []))
            sprechzeiten = "<br>".join(entry.get("sprechzeiten", []))
            kontaktiert = ""  # Placeholder for Kontaktiert
            datum = ""  # Placeholder for Datum
            status = ""  # Placeholder for Status
            
            md_file.write(f"| {name} | {address} | {telefon} | {email} | {therapieform} | {sprechzeiten} | {kontaktiert} | {datum} | {status} |\n")

def create_email_drafts(data, user_info):
    email_template = """
<ANREDE>,

mein Name ist {name} und ich bin auf der Suche nach einem Platz für eine Psychotherapie. Aus diesem Grund möchte ich Sie fragen, ob Sie dafür grundlegend Kapazitäten hätten und mir in absehbarer Zeit einen Termin für eine Probestunde anbieten können.

Ich hatte bereits ein Erstgespräch bei einem Ihrer Kollegen. In diesem Rahmen wurde mir ein Überweisungsschein und eine Empfehlung für den Beginn einer Therapie (mit der Bemerkung ”zeitnah erforderlich”) ausgestellt. Da ich Kassenpatient bin, suche ich vorrangig PsychotherapeutInnen mit Kassensitz.

Falls benötigt, ist hier der Vermittlungscode: {vermittlungscode}

Außerdem hier meine Kontaktdaten:
{name}
{address}
Tel.: {telefon}
Mail: {email}

Sollten Sie noch weitere Daten von mir benötigen, kontaktieren Sie mich gerne! Auch wenn Sie mir keinen Platz anbieten können, würde ich mich über eine kurze Absage freuen.

Ich wünsche einen schönen Tag!

Mit freundlichen Grüßen

{name}
"""

    drafts = []

    for entry in data:
        if "email" in entry:
            name = entry["name"]
            # Extract title and last name
            title_match = re.search(r'(Dr\.|Dipl\.-Psych\.)', name)
            title = title_match.group(0) if title_match else ""
            last_name = name.split()[-1]
            if "Frau" in name:
                anrede = f"Sehr geehrte Frau {title} {last_name}"
            elif "Herr" in name:
                anrede = f"Sehr geehrter Herr {title} {last_name}"
            else:
                anrede = f"Sehr geehrte/r {title} {last_name}"

            email_body = email_template.replace("<ANREDE>", anrede).format(
                name=user_info["name"],
                address=user_info["address"],
                telefon=user_info["telefon"],
                email=user_info["email"],
                vermittlungscode=user_info["vermittlungscode"]
            )
            drafts.append({
                "to": entry["email"],
                "subject": "Anfrage für Analytische Psychotherapie",
                "body": email_body
            })

    return drafts

# Example file path (replace with your actual file path)
file_path = 'Arzt-_und_Psychotherapeutensuche_der_KV_Berlin.txt'
parsed_data = parse_psychotherapists(file_path)

# Save parsed data to JSON
with open('parsed_data.json', 'w', encoding='utf-8') as json_file:
    json.dump(parsed_data, json_file, ensure_ascii=False, indent=4)

# Create Markdown table
markdown_file_path = 'parsed_data.md'
create_markdown_table(parsed_data, markdown_file_path)

# Create email drafts with user info
email_drafts = create_email_drafts(parsed_data, user_info)

# Save email drafts to JSON for review
with open('email_drafts.json', 'w', encoding='utf-8') as email_file:
    json.dump(email_drafts, email_file, ensure_ascii=False, indent=4)

# Print email drafts to console for review
print(json.dumps(email_drafts, ensure_ascii=False, indent=4))
