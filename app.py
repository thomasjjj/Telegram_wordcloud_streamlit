import streamlit as st
import asyncio
import nest_asyncio
import os
import re
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from wordcloud import WordCloud
from stop_words import get_stop_words
import matplotlib.pyplot as plt

# Allow nested event loops (required for running async code in Streamlit)
nest_asyncio.apply()

# ----------------------------------------------------
# Messages Dictionary (English only)
# ----------------------------------------------------
MESSAGES = {
    "title": "Telegram Post Wordcloud Generator",
    "overview": (
        "This tool signs you into Telegram and then downloads all posts from a specified channel or chat. "
        "It generates a wordcloud of the main words found in the posts, with common stopwords removed "
        "for English, Ukrainian and Russian."
    ),
    "step1": "Step 1: Sign in to Telegram",
    "enter_api_id": "Enter your API ID",
    "enter_api_hash": "Enter your API Hash",
    "enter_phone": "Enter your phone number (with country code, e.g. +441234567890)",
    "sign_in": "Sign In",
    "credentials_missing": "Please provide all credentials (API ID, API Hash and phone number).",
    "api_id_int_error": "API ID must be an integer.",
    "signing_in_spinner": "Signing in to Telegram...",
    "sign_in_success": "Signed in successfully!",
    "sign_in_error_prefix": "An error occurred during sign in: ",
    "awaiting_code_msg": "An authentication code has been sent. Please enter it below.",
    "enter_auth_code": "Enter authentication code",
    "submit_code": "Submit Code",
    "signing_in_with_code_spinner": "Signing in...",
    "enter_password": "Enter your 2FA password",
    "submit_password": "Submit Password",
    "2fa_required": "Two‐factor authentication is enabled. Please enter your password.",
    "step2": "Step 2: Enter Channel or Chat Link",
    "enter_channel": "Enter a Telegram channel or chat link (e.g. https://t.me/channelname or https://t.me/c/123456789/1)",
    "download_button": "Download Posts and Generate Wordcloud",
    "processing_channel": "Processing channel…",
    "download_progress": "Downloaded {count} posts out of {total}",
    "wordcloud_generated": "Wordcloud generated below:",
    "reset_session": "Reset Session",
    "reset_success": "Session reset successfully."
}

st.title(MESSAGES["title"])
st.markdown(MESSAGES["overview"])

# ----------------------------------------------------
# Asynchronous Helper: Create and return a Telegram client
# ----------------------------------------------------
async def async_get_client(api_id, api_hash, phone):
    client = TelegramClient("session_" + phone, api_id, api_hash)
    await client.connect()
    return client

# ----------------------------------------------------
# Session management for the asyncio loop
# ----------------------------------------------------
if "loop" not in st.session_state:
    st.session_state.loop = asyncio.new_event_loop()

# Reset session button: Disconnect client and remove session file
if st.button(MESSAGES["reset_session"]):
    if "client" in st.session_state:
        st.session_state.loop.run_until_complete(st.session_state.client.disconnect())
        del st.session_state.client
    for f in os.listdir("."):
        if f.startswith("session_") and f.endswith(".session"):
            os.remove(f)
    st.success(MESSAGES["reset_success"])

# ----------------------------------------------------
# Telegram Sign-In UI
# ----------------------------------------------------
st.header(MESSAGES["step1"])
api_id_input = st.text_input(MESSAGES["enter_api_id"])
api_hash_input = st.text_input(MESSAGES["enter_api_hash"])
phone_input = st.text_input(MESSAGES["enter_phone"])

if st.button(MESSAGES["sign_in"]):
    if not api_id_input or not api_hash_input or not phone_input:
        st.error(MESSAGES["credentials_missing"])
    else:
        try:
            api_id_int = int(api_id_input)
        except ValueError:
            st.error(MESSAGES["api_id_int_error"])
        else:
            with st.spinner(MESSAGES["signing_in_spinner"]):
                try:
                    client = st.session_state.loop.run_until_complete(
                        async_get_client(api_id_int, api_hash_input, phone_input)
                    )
                    st.session_state.client = client
                    st.session_state.phone = phone_input
                    if not st.session_state.loop.run_until_complete(client.is_user_authorized()):
                        st.session_state.loop.run_until_complete(client.send_code_request(phone_input))
                        st.session_state.awaiting_code = True
                        st.info(MESSAGES["awaiting_code_msg"])
                    else:
                        st.success(MESSAGES["sign_in_success"])
                except Exception as e:
                    st.error(MESSAGES["sign_in_error_prefix"] + str(e))

# ----------------------------------------------------
# Two-Step Authentication: Enter Code if Required
# ----------------------------------------------------
if st.session_state.get("awaiting_code", False):
    auth_code = st.text_input(MESSAGES["enter_auth_code"], key="auth_code")
    if st.button(MESSAGES["submit_code"]):
        with st.spinner(MESSAGES["signing_in_with_code_spinner"]):
            try:
                st.session_state.loop.run_until_complete(
                    st.session_state.client.sign_in(phone_input, auth_code)
                )
                st.success(MESSAGES["sign_in_success"])
                st.session_state.awaiting_code = False
            except SessionPasswordNeededError:
                st.info(MESSAGES["2fa_required"])
                st.session_state.awaiting_code = False
                st.session_state.awaiting_password = True
            except Exception as e:
                st.error(MESSAGES["sign_in_error_prefix"] + str(e))

if st.session_state.get("awaiting_password", False):
    password = st.text_input(MESSAGES["enter_password"], type="password", key="password")
    if st.button(MESSAGES["submit_password"]):
        with st.spinner(MESSAGES["signing_in_with_code_spinner"]):
            try:
                st.session_state.loop.run_until_complete(
                    st.session_state.client.sign_in(password=password)
                )
                st.success(MESSAGES["sign_in_success"])
                st.session_state.awaiting_password = False
            except Exception as e:
                st.error(MESSAGES["sign_in_error_prefix"] + str(e))

# ----------------------------------------------------
# Process the Channel/Chat Link to Extract Identifier
# ----------------------------------------------------
def process_channel_link(link):
    # Check for /c/ format first
    pattern_chat = r"(?:https?://)?t\.me/c/(\d+)"
    match_chat = re.search(pattern_chat, link)
    if match_chat:
        # For t.me/c/ links, convert numeric part to channel ID
        channel_id = -(int(match_chat.group(1)) + 1000000000000)
        return channel_id
    # Otherwise, assume a standard username link (which may include a post ID)
    pattern_username = r"(?:https?://)?t\.me/([A-Za-z0-9_]+)"
    match_username = re.search(pattern_username, link)
    if match_username:
        return match_username.group(1)
    return None

# ----------------------------------------------------
# Download Posts and Generate Combined Text for Wordcloud
# ----------------------------------------------------
async def download_posts(client, channel_identifier):
    try:
        # Retrieve the channel entity using the identifier.
        entity = await client.get_entity(channel_identifier)
    except Exception as e:
        st.error("Error retrieving channel: " + str(e))
        return None

    # Use the latest post to approximate the total number of posts.
    top_msg = await client.get_messages(entity, limit=1)
    total_posts = top_msg[0].id if top_msg else 0

    texts = []
    count = 0
    progress_bar = st.progress(0)
    progress_text = st.empty()

    # Iterate through messages in ascending order.
    async for message in client.iter_messages(entity, reverse=True):
        count += 1
        if message.message:
            texts.append(message.message)
        if total_posts:
            progress = min(int((count / total_posts) * 100), 100)
            progress_bar.progress(progress)
            progress_text.text(MESSAGES["download_progress"].format(count=count, total=total_posts))
    return " ".join(texts)

# ----------------------------------------------------
# Channel Input & Wordcloud Generation UI
# ----------------------------------------------------
if ("client" in st.session_state and 
    not st.session_state.get("awaiting_code", False) and 
    not st.session_state.get("awaiting_password", False)):
    
    st.header(MESSAGES["step2"])
    channel_link_input = st.text_input(MESSAGES["enter_channel"])
    
    if st.button(MESSAGES["download_button"]):
        if channel_link_input:
            channel_identifier = process_channel_link(channel_link_input)
            if channel_identifier is None:
                st.error("Channel link not recognised.")
            else:
                with st.spinner(MESSAGES["processing_channel"]):
                    full_text = st.session_state.loop.run_until_complete(
                        download_posts(st.session_state.client, channel_identifier)
                    )
                if full_text:
                    # Retrieve stopwords for English, Ukrainian and Russian
                    english_stopwords = set(get_stop_words('en'))
                    ukrainian_stopwords = set(get_stop_words('uk'))
                    russian_stopwords = set(get_stop_words('ru'))
                    combined_stopwords = english_stopwords.union(ukrainian_stopwords).union(russian_stopwords)
                    
                    # Generate and display the wordcloud using the combined stopwords.
                    wc = WordCloud(
                        stopwords=combined_stopwords,
                        width=800,
                        height=400,
                        background_color="white"
                    ).generate(full_text)
                    st.image(wc.to_array(), caption=MESSAGES["wordcloud_generated"])
                else:
                    st.warning("No posts found or an error occurred during download.")
        else:
            st.error("Please enter a channel or chat link.")
