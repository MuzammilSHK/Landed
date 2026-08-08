"""Orchestration between the domain core and persistence.

Route handlers stay thin by calling into here; nothing in this layer knows about
HTTP requests or templates.
"""
