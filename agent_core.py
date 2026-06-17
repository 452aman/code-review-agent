from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List
import os


load_dotenv()

class Bug(BaseModel):
    description: str = Field(description = "What is wrong with the code")
    severity : str = Field(description = "Severity level: High, Medium, Low")
    fix: str = Field(description="Specific suggestion to fix the bug")

class ReviewResult(BaseModel):
    bugs: list[Bug] = Field(description="List of all bugs found")
    total_bugs: int = Field(description="Total no of bugs found")
    has_critical: bool = Field(description="True if any bug has HIGH severity") 


llm = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=os.getenv("GROQ_API_KEY")
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a senior software engineer doing a thorough code review.
    Analyse the code and identify ALL bugs, issues, and bad practices.
    For each bug provide a clear description, severity level, and fix.
    Be specific and technical in your analysis. Return your response as a JSON object."""),
    ("user", "Language: {language}\n\nReview this code:\n```\n{code}\n```")
])

structured_llm = llm.with_structured_output(ReviewResult)
chain = prompt | structured_llm
def review_code(code : str, language : str = "python") -> ReviewResult:
    return chain.invoke({"code": code, "language": language})

if __name__ == "__main__":
    buggy_code = """
def divide(a, b):
    result = a / b
    return result

def get_user(users, id):
    return users[id]

print(divide(10, 0))
print(get_user(["alice", "bob"], 5))
"""

    result = review_code(buggy_code)

    print(f"\nTotal bugs found : {result.total_bugs}")
    print(f"Has critical bugs: {result.has_critical}\n")

    for i, bug in enumerate(result.bugs, 1):
        print(f"Bug {i}:")
        print(f"  Description : {bug.description}")
        print(f"  Severity    : {bug.severity}")
        print(f"  Fix         : {bug.fix}")
        print()