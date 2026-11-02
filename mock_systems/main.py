from fastapi import FastAPI

app = FastAPI(title="Mock Systems")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
