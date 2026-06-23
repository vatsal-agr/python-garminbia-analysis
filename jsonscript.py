from google_auth_oauthlib.flow import InstalledAppFlow
import json

flow = InstalledAppFlow.from_client_secrets_file(
    "gmail_oauth_client.json",
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
)

flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
auth_url, _ = flow.authorization_url(prompt="consent")

print("Open this URL in your browser:")
print(auth_url)

code = input("Paste the authorization code here: ")
flow.fetch_token(code=code)
creds = flow.credentials

print(json.dumps({
    "token": creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "scopes": list(creds.scopes),
}, indent=2))