from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os

load_dotenv()

# The LLM — Groq runs LLaMA3 on their fast LPU chip, completely free
llm = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=os.getenv("GROQ_API_KEY")
)

# The prompt — system message tells LLM its role + exact output format
# {code} and {language} are placeholders filled at runtime
prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a senior software engineer doing a code review.
Analyse the code carefully and identify all bugs, issues, and bad practices.

For each issue found, respond in EXACTLY this format:
Bug: <what is wrong>
Severity: HIGH / MEDIUM / LOW
Fix: <specific fix suggestion>

---

Severity guide:
HIGH   = will crash or produce wrong output
MEDIUM = bad practice, edge case risk
LOW    = style, readability, minor issue

List every issue you find. Be concise and specific."""),

    ("user", "Language: {language}\n\nCode to review:\n```\n{code}\n```")
])

# Parses the raw LLM response object → plain string
parser = StrOutputParser()

# LCEL pipe: input flows through prompt → llm → parser
chain = prompt | llm | parser


def review_code(code: str, language: str = "Python") -> str:
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

    print("=== CODE REVIEW AGENT — chain.py ===\n")
    print("Reviewing code...\n")
    result = review_code(buggy_code)
    print(result)
