import uuid

from django.test import Client

tag = uuid.uuid4().hex[:6]
client = Client(HTTP_HOST="localhost")
payload = {
    "email": f"owner-{tag}@example.com",
    "password": "A-long-test-password-1",
    "full_name": "Test Owner",
    "company_name": f"Gamma {tag}",
}

r = client.post("/api/v1/register/", payload, content_type="application/json")
print("register:", r.status_code, r.json()["organization"]["name"])
org_id = r.json()["organization"]["id"]

r = client.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=org_id)
print("me:", r.status_code, r.json()["email"])

r = client.get("/api/v1/roles/", HTTP_X_ORGANIZATION_ID=org_id)
print("roles:", r.status_code, len(r.json()))

r = client.post("/api/v1/register/", payload, content_type="application/json")
print("register again:", r.status_code)

weak = {**payload, "email": f"weak-{tag}@example.com", "password": "123"}
r = client.post("/api/v1/register/", weak, content_type="application/json")
print("weak password:", r.status_code)
