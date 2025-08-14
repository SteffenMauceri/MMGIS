from openai import OpenAI
import os, json, subprocess          # or your own app-control code

client = OpenAI(
    api_key="unused",                # Ollama/vLLM ignore it
    base_url="http://localhost:11434/v1"  # Ollama default
)

FUNCTIONS = [{
    "name": "query_external_api",
    "description": "Call the FooBar REST API",
    "parameters": {
        "type": "object",
        "properties": {"endpoint": {"type": "string"}},
        "required": ["endpoint"]
    }
}, {
    "name": "control_player",
    "description": "Send a command to my local app",
    "parameters": { "type": "object",
        "properties": { "cmd": {"type": "string"} },
        "required": ["cmd"]
    }
}]

messages = [{"role":"user","content":"Play the next podcast episode"}]

while True:
    rsp = client.chat.completions.create(
        model="gpt-oss:20b",
        messages=messages,
        tools=FUNCTIONS,
        tool_choice="auto"     # let the model decide
    )
    msg = rsp.choices[0].message
    messages.append(msg)       # keep context

    if msg.tool_calls:
        for call in msg.tool_calls:
            if call.name == "query_external_api":
                result = query_api(**json.loads(call.args_json))
            elif call.name == "control_player":
                result = subprocess.run(
                    ["playerctl", json.loads(call.args_json)["cmd"]],
                    capture_output=True, text=True
                ).stdout
            messages.append({"role":"tool","name":call.name,"content":result})
    else:
        print(msg.content)
        break
