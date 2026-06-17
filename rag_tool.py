from langchain_community.vectorstores import FAISS                                                                               
from langchain_huggingface import HuggingFaceEmbeddings                                                                          
from langchain.tools.retriever import create_retriever_tool

DOCS = [                                                                                                                         
      "Always handle exceptions in Python using try/except blocks to prevent crashes.",
      "Use list comprehensions instead of for loops for better performance and readability.",                                      
      "Never use mutable default arguments in functions, use None and set inside the function.",                                 
      "Use f-strings for string formatting in Python 3.6+.",
      "Always close file handles using context managers (the with statement).",
      "Avoid global variables — pass data through function parameters instead.",
      "Use type hints to make code more readable and catch bugs early.",                                                           
      "Write docstrings for all public functions, classes, and modules.",
    ]


embeddings = HuggingFaceEmbeddings(model_name = "all-MiniLM-L6-v2")
vector_store = FAISS.from_texts(DOCS, embeddings)
retriever = vector_store.as_retriever(search_kwargs = {"k" : 2})

search_docs = create_retriever_tool(                                                                                             
    retriever,
    name="search_python_docs",                                                                                                   
    description="Search Python best practices documentation. Use this when reviewing code to find relevant coding standards and best practices."                                                                                                                 
) 

if __name__ == "__main__":                                                                                                       
    print("=== Testing search_docs ===")
    result = search_docs.invoke("how to handle errors in python")                                                                
    print(result)
