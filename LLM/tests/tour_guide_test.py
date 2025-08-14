import asyncio
import json
from langchain_core.messages import HumanMessage
from NLP_UI import create_agent

# A small, hard-coded knowledge base for our tour guide
KNOWLEDGE_BASE = {
    "los angeles": {
        "description": "Los Angeles, a sprawling Southern California city and the center of the nation’s film and television industry.",
        "coordinates": [34.0522, -118.2437],
        "zoom": 10
    },
    "paris": {
        "description": "Paris, France's capital, is a major European city and a global center for art, fashion, gastronomy and culture.",
        "coordinates": [48.8566, 2.3522],
        "zoom": 12
    },
    "gale crater": {
        "description": "Gale Crater on Mars, the landing site of the Curiosity rover.",
        "coordinates": [-5.4, 137.8],
        "zoom": 8
    },
    "jezero crater": {
        "description": "Jezero Crater, the landing site for the Perseverance rover, known for its ancient river delta.",
        "coordinates": [18.4, 77.6],
        "zoom": 9
    },
    "olympus mons": {
        "description": "Olympus Mons, a massive shield volcano on Mars and the tallest volcano in the solar system.",
        "coordinates": [18.6, -133.8],
        "zoom": 6
    },
     "valles marineris": {
        "description": "Valles Marineris, a vast canyon system on Mars, one of the largest in the solar system.",
        "coordinates": [-13.9, -59.2],
        "zoom": 5
    }
}

async def run_tour_guide_test():
    """
    Runs a test of the tour guide functionality.
    """
    print("--- Starting Tour Guide Test ---")
    print("\nIMPORTANT: Before running, please ensure you have a Chrome browser running that was launched with the remote debugging port open.")
    print("You can do this by running the following command in your terminal:")
    print('"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$(mktemp -d)"')
    print("Then, open MMGIS at http://localhost:8888 in that browser instance.\n")


    # The user's request
    user_prompt = "Show me Los Angeles on the map."
    location_name = "los angeles"
    
    # Augment the prompt with information from our knowledge base
    location_info = KNOWLEDGE_BASE.get(location_name)
    if not location_info:
        print(f"Sorry, I don't have information about {location_name}.")
        return

    augmented_prompt = (
        f"The user wants to see '{location_name}'. "
        f"Description: {location_info['description']}. "
        f"Coordinates are {location_info['coordinates']} with a zoom level of {location_info['zoom']}. "
        f"Use the API to pan the map to these coordinates."
    )

    print(f"\nUser Prompt: {user_prompt}")
    print(f"Augmented Prompt Sent to Agent: {augmented_prompt}\n")

    # Create the agent and messages
    agent = create_agent()
    messages = [HumanMessage(content=augmented_prompt)]

    # Stream the agent's response
    async for chunk in agent.astream({"messages": messages}):
        print("--- Next Chunk ---")
        print(chunk)

if __name__ == "__main__":
    asyncio.run(run_tour_guide_test())
