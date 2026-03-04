import os
import requests

import time
from langchain_openai import AzureChatOpenAI
from langchain_community.callbacks.manager import get_openai_callback

from dotenv import load_dotenv, find_dotenv

from azure.identity import DefaultAzureCredential
from lib.util import extract_json_from_GPTresponse

_ = load_dotenv(find_dotenv(raise_error_if_not_found=False))

OAI_ACCESS_KEY = os.getenv("OAI_ACCESS_KEY")
OAI_ACCESS_ENDPOINT = os.getenv("OAI_ACCESS_ENDPOINT")
OAI_ACCESS_MODEL_NAME = os.getenv("OAI_ACCESS_MODEL_NAME", "")
OAI_ACCESS_DEPLOYMENT_NAME = os.getenv("OAI_ACCESS_DEPLOYMENT_NAME", "")

AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "")
AZURE_OPENAI_RESOURCE_NAME = os.getenv("AZURE_OPENAI_RESOURCE_NAME", "")

APIVERSION = os.getenv("APIVERSION", "")

SEED = int(os.getenv("INITIAL_SEED", 400))


def content_logging_is_disabled(subscription_id, resource_group, resource_name) -> bool:
    """
    Checks whether ContentLogging is disabled for an Azure OpenAI resource.

    Args:
        subscription_id (str): Your Azure subscription ID.
        resource_group (str): The name of the resource group.
        resource_name (str): The name of the Azure OpenAI resource.

    Returns:
        bool: True if ContentLogging is disabled (i.e., content_logging_disabled set to True), False otherwise.
    """

    print(
        f"Checking ContentLogging for resource '{resource_name}' in resource group '{resource_group}'..."
    )

    credential = DefaultAzureCredential()

    token = credential.get_token("https://management.azure.com/.default").token

    # Construct the URL
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.CognitiveServices/accounts/{resource_name}"
        f"?api-version=2023-05-01"
    )

    # Make the request
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    data = response.json()

    capabilities = data.get("properties", {}).get("capabilities", {})

    content_logging_cap = next(
        (item for item in capabilities if item.get("name") == "ContentLogging"), None
    )

    if content_logging_cap is None:
        print("ContentLogging not found in capabilities.")
        content_logging_disabled = False
    else:
        print("ContentLogging capability found.")
        value = content_logging_cap.get("value", "").strip().lower()
        print(f"ContentLogging tag value: '{value}'")
        if value == "false":
            content_logging_disabled = True
        else:
            content_logging_disabled = False
    return content_logging_disabled


def openai_request_viaAzureOpenAI(
    system_message: str, user_message: str, temperature: float
) -> str:
    """
    Sends a request to Azure OpenAI chat completions API using a streaming approach.

    Args:
        system_message (str): The system message to be included in the chat conversation.
        user_message (str): The user message to be included in the chat conversation.
        temperature_input (float): The temperature value for controlling the randomness of the generated responses.

    Returns:
        answer: The response answer from the Azure Chat OpenAI API from Langchain.
    """

    client = AzureChatOpenAI(
        deployment_name=OAI_ACCESS_DEPLOYMENT_NAME,
        api_key=OAI_ACCESS_KEY,
        azure_endpoint=OAI_ACCESS_ENDPOINT,
        # api_version = "2024-12-01-preview",
        api_version=APIVERSION,
        temperature=temperature,
        seed=SEED,
        disable_streaming=True,
    )

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]

    with get_openai_callback() as cb:
        stream = client.invoke(messages)
        answer = stream.content
        return answer, cb


def run_audit_check_and_gpt(
    system_message: str, user_message: str, temperature: float
) -> str:
    """
    Runs an audit check to verify if ContentLogging is disabled for the Azure OpenAI resource.
    If the check passes, it calls the GPT model to get a response.

    Args:
        system_message (str): The system message for the GPT model.
        user_message (str): The user message for the GPT model.
        temperature (float): The temperature setting for the GPT model.

    Returns:
        answer (str) : The response from the GPT model if ContentLogging is disabled.
    """

    if content_logging_is_disabled(
        AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_OPENAI_RESOURCE_NAME
    ):
        print("✅ ContentLogging is disabled. Proceeding with GPT request.")
        answer = openai_request_viaAzureOpenAI(
            system_message, user_message, temperature
        )
        return answer

    else:
        print("⚠️ ContentLogging is enabled or not set. Skipping GPT request.")
        return "", "ContentLogging is enabled; GPT request skipped."


def run_with_retries(
    system_message: str,
    user_message: str,
    temperature: float,
    max_retries: int = 3,
    delay: int = 2,
):
    """Wrapper that retries GPT call + JSON extraction up to max_retries times."""
    for attempt in range(1, max_retries + 1):
        try:
            response, stats = run_audit_check_and_gpt(
                system_message=system_message,
                user_message=user_message,
                temperature=temperature,
            )
            json_data = extract_json_from_GPTresponse(response)
            return json_data, stats
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(delay)  # small wait before retry
            else:
                raise  # give up after max retries
