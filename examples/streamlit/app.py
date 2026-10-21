"""
A Streamlit UI hosted by woodglue. It greets the viewing User and calls the
woodglue API as them, so it can only do what their Grants allow.

Run with the `woodglue.yaml` next to this file:

    uv run --with streamlit wgl --data examples/streamlit start
"""

import asyncio

import streamlit as st

from woodglue.client import WoodglueRpcError
from woodglue.ui_support import current_client, current_user

st.title("Hello from woodglue")

me = current_user()
if me is None:
    st.warning("Open this app through woodglue at /ui/hello/.")
    st.stop()

st.write(f"Signed in as **{me.name}** ({me.email or 'no email'}), roles: {', '.join(me.roles)}")

name = st.text_input("Name to measure", "World")


async def measure(text: str) -> int:
    return await current_client().call("hello.woodglue.hello:hello", name=text)


try:
    st.metric("Length", asyncio.run(measure(name)))
except WoodglueRpcError as exc:
    st.error(f"The API refused: {exc}")
