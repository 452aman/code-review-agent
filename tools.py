from langchain_core.tools import tool
import subprocess

@tool 
def run_code(code: str) -> str:
    """Execute Python code and return the output or error message."""
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output= True,
            text= True,
            timeout= 10
        )
        if result.returncode == 0:
            return f"Output: {result.stdout}"
        else:
               return f"Error: {result.stderr}"                                                                                     
    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out after 10 seconds."   
    
@tool
def search_web(query: str) -> str:
    """Search the web for information about a programming error or concept."""
    try:                                                                                                                         
        result = subprocess.run(
            ["python3", "-c", f"""                                                                                               
            import urllib.request, urllib.parse, json                                                                                        
            query = urllib.parse.quote("{query}")
            url = f"https://api.duckduckgo.com/?q={{query}}&format=json&no_html=1"                                                           
            req = urllib.request.urlopen(url, timeout=5)                                                                                     
            data = json.loads(req.read().decode())
            abstract = data.get("Abstract", "")                                                                                              
            related = [r.get("Text","") for r in data.get("RelatedTopics", [])[:3] if r.get("Text")]
            result = abstract if abstract else " | ".join(related)                                                                           
            print(result if result else "No results found.")                                                                                 
            """],                                                                                                                            
            capture_output=True, text=True, timeout=10                                                                           
        )                                                                                                                        
        return result.stdout.strip() or "No results found."
    except Exception as e:
        return f"Search failed: {str(e)}"


if __name__ == "__main__":
    print("=== Testing run_code ===")
    print(run_code.invoke("print(2 + 2)"))

    print("\n=== Testing search_web ===")
    print(search_web.invoke("what is a python decorator"))