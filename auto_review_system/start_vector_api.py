import uvicorn
import os
import sys

# Add the project root to PYTHONPATH so we can resolve 'auto_review_system' modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    print("Starting Vector Store API on port 8001...")
    # Run the FastAPI app
    uvicorn.run("auto_review_system.rag_engine.api:app", host="0.0.0.0", port=8001, reload=False)
