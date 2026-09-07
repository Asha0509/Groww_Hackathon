from fastapi import FastAPI

app = FastAPI(title="Since")

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "since"}

@app.get("/")
def root():
    return {"service": "since", "status": "scaffold"}
