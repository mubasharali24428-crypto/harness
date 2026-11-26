"""RC :3011 manual-check fixture. Real APIs only (dev email-only sign-in on the local RC stack).
Tokens go to state.json (0600) for later checks; stdout carries ids and status codes only."""
import json, os, sys, time, urllib.request, urllib.error

ID = "http://127.0.0.1:18001/api/v1"
ORCH = "http://127.0.0.1:18004/api/v1/orchestrator"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")


def call(url, token="", method="GET", data=None):
    req = urllib.request.Request(url, method=method, data=None if data is None else json.dumps(data).encode())
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return e.code, (json.loads(body) if body.startswith("{") else body)


def must(label, res, want):
    code, body = res
    print(f"{label}: {code}")
    if code != want:
        sys.exit(f"{label} expected {want}, got {code}: {body}")
    return body


def join(admin_tok, team, email, role):
    code, t = call(f"{ID}/auth/login", method="POST", data={"email": email, "teamId": team})
    if code == 200:  # already a member from an earlier pass
        print(f"login team {role} (existing): 200")
        return {"email": email, "token": t["token"], "userId": t["principal"]["userId"], "role": t["principal"]["role"]}
    solo = must(f"login solo {role}", call(f"{ID}/auth/login", method="POST", data={"email": email}), 200)
    inv = must(f"mint invite {role}", call(f"{ID}/teams/{team}/invites", admin_tok, "POST", {"email": email, "role": role}), 201)
    must(f"redeem {role}", call(f"{ID}/invites/redeem", solo["token"], "POST", {"token": inv["token"], "email": email}), 200)
    t = must(f"login team {role}", call(f"{ID}/auth/login", method="POST", data={"email": email, "teamId": team}), 200)
    return {"email": email, "token": t["token"], "userId": t["principal"]["userId"], "role": t["principal"]["role"]}


if __name__ == "__main__":
    tag = sys.argv[1]
    admin_email = sys.argv[2]
    a = must("login admin", call(f"{ID}/auth/login", method="POST", data={"email": admin_email}), 200)
    admin = {"email": admin_email, "token": a["token"], "userId": a["principal"]["userId"],
             "teamId": a["principal"]["teamId"], "role": a["principal"]["role"]}
    team = admin["teamId"]
    eng = join(admin["token"], team, f"uilive-eng-{tag}@harness.dev", "engineer")
    viewer = join(admin["token"], team, f"uilive-viewer-{tag}@harness.dev", "viewer")
    plan = must("plan", call(f"{ORCH}/plans", admin["token"], "POST", {"goal": f"UI live check {tag}: rename the greeting helper", "projectId": sys.argv[3]}), 201)
    run = plan["runId"]
    for who, t in (("admin", admin["token"]), ("engineer", eng["token"])):
        must(f"join {who}", call(f"{ORCH}/runs/{run}/join", t, "POST"), 200)
    must("leader admin", call(f"{ORCH}/runs/{run}/leader", admin["token"], "POST"), 200)
    asks = [
        {"action": "modify", "paths": ["src/greeting.ts"], "summary": "Rename greet to greetUser", "diff": "-export function greet\n+export function greetUser"},
        {"action": "delete", "paths": ["src/old_helper.ts"], "summary": "Remove the unused helper", "diff": "-export const old = 1"},
        {"action": "modify", "paths": ["docs/readme.md"], "summary": "Document the rename", "diff": "-greet\n+greetUser"},
    ]
    ids = [must(f"ask {i}", call(f"{ORCH}/runs/{run}/approvals", eng["token"], "POST", ask), 201)["id"] for i, ask in enumerate(asks)]
    state = {"admin": admin, "engineer": eng, "viewer": viewer, "runId": run, "projectId": plan.get("projectId"), "approvalIds": ids}
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    print(json.dumps({"teamId": team, "runId": run, "projectId": plan.get("projectId"), "approvalIds": ids,
                      "engineer": eng["userId"], "viewer": viewer["userId"], "roles": [admin["role"], eng["role"], viewer["role"]]}))
