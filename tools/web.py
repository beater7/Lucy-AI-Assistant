from ddgs import DDGS
from ddgs.exceptions import DDGSException

from model.ollama_client import summarize_search_results


def search_web(query):
    try:
        with DDGS(timeout=15) as search_client:
            results = search_client.text(query, region="wt-wt", max_results=5)
    except DDGSException as error:
        raise RuntimeError(f"DuckDuckGo search failed: {error}") from error
    if not results:
        raise ValueError("The web search didn't return any results.")

    answer, sources = summarize_search_results(query, results)
    return answer, sources, "DuckDuckGo"
