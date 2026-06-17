from langchain_core.messages import HumanMessage                                                                                 
from graph import graph                                                                                                        
import gradio as gr 

def chat(message, history):                                                                                                      
    result = graph.invoke({"messages": [HumanMessage(content=message)]})                                                       
    return result["messages"][-1].content

demo = gr.ChatInterface(                                                                                                         
    fn=chat,                                                                                                                   
    title="Code Review Agent",
    description="Paste your Python code and I'll review it for bugs, best practices, and improvements.",
    examples=["Review this code: def divide(a, b): return a / b"],                                                               
)

if __name__ == "__main__":                                                                                                       
    demo.launch()
