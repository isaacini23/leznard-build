import uuid

from django.test import Client

tag = uuid.uuid4().hex[:6]
email = f"login-{tag}@example.com"
password = "A-long-test-password-1"

Client(HTTP_HOST="localhost").post(
    "/api/v1/register/",
    {"email": email, "password": password, "full_name": "Login Tester", "company_name": f"Delta {tag}"},
    content_type="application/json",
)

client = Client(HTTP_HOST="localhost")
print("me before login:", client.get("/api/v1/me/").status_code)

r = client.post("/api/v1/login/", {"email": email, "password": "wrong-password"}, content_type="application/json")
print("wrong password:", r.status_code)

r = client.post("/api/v1/login/", {"email": email, "password": password}, content_type="application/json")
print("login:", r.status_code, [o["name"] for o in r.json()["organizations"]])

print("me after login:", client.get("/api/v1/me/").status_code)
print("logout:", client.post("/api/v1/logout/").status_code)
print("me after logout:", client.get("/api/v1/me/").status_code)
