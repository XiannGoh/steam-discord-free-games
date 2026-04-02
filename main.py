import os
import requests

webhook = os.getenv("DISCORD_WEBHOOK_URL")

message = {
    "content": "Hello! My Steam Discord automation is working."
}

response = requests.post(webhook, json=message)

print(response.status_code)
print(response.text)
