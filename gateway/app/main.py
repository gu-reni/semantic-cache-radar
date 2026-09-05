from fastapi import FastAPI

app=FastAPI(
    title="Semantic Cache Gateway",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict[str,str]:
    """Return a lightweight liveness response without external dependencies."""
    return {"status": "ok"}