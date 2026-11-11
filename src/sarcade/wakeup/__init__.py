"""Waking the devices when SARCADE is closed (note « Réveil des appareils »,
validated on 28 October 2026).

The server only sends a signal without content (« new for event X »,
priority); the device synchronises through SARCADE and shows its own
notification. Google, Apple and the browser push services never see the
text of a message.
"""
