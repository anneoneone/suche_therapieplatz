import json

def create_applescript(email_drafts):
    script = """
    tell application "Mail"
        activate
    """
    for draft in email_drafts:
        to = draft["to"]
        subject = draft["subject"]
        body = draft["body"].replace("\n", "\\n").replace('"', '\\"')
        script += f"""
        set newMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}\\n"}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{to}"}}
            save
        end tell
        """
    script += "end tell"
    return script

# Load email drafts from JSON
with open('email_drafts.json', 'r', encoding='utf-8') as email_file:
    email_drafts = json.load(email_file)

# Create AppleScript
applescript = create_applescript(email_drafts)

# Save AppleScript to file
with open('create_email_drafts.scpt', 'w', encoding='utf-8') as script_file:
    script_file.write(applescript)

print("AppleScript file 'create_email_drafts.scpt' created.")