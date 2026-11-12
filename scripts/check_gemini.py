import os

from dotenv import load_dotenv
from google import genai

load_dotenv("backend/.env")

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing from backend/.env")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
   model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    contents="""
Return ONLY valid JSON.

{
  "status": "success",
  "message": "Gemini ESG Intelligence Agent test successful"
}
"""
)

print(response.text)