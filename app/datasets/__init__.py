"""Dataset provenance, licence gating and validation for files supplied outside the connectors.

Nothing here talks to the network or the database. Everything is pure so it can be tested on the
real files' structure. Original files are never modified: the manifest records their SHA-256.
"""
