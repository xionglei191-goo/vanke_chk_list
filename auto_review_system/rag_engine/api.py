from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

# Import the existing vector store retrieval function
from rag_engine.vector_store import retrieve_rules, init_vector_db

app = FastAPI(
    title="Vector Store Retrieval API",
    description="API for querying the RAG knowledge base of Vanke standards.",
    version="1.0.0"
)

class RetrieveRequest(BaseModel):
    query: str
    wbs_code: Optional[str] = None
    n_results: Optional[int] = 2

class RetrieveResponse(BaseModel):
    results: str

@app.on_event("startup")
async def startup_event():
    # init_vector_db() is called by default on import in vector_store.py
    # but could be triggered here if there is a delayed loading mechanism.
    pass

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/api/v1/retrieve", response_model=RetrieveResponse)
async def retrieve_endpoint(request: RetrieveRequest):
    try:
        # retrieve_rules returns a string joined by '\n' based on current implementation
        results = retrieve_rules(
            query=request.query,
            wbs_code=request.wbs_code,
            n_results=request.n_results
        )
        return RetrieveResponse(results=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("rag_engine.api:app", host="0.0.0.0", port=8001, reload=True)
