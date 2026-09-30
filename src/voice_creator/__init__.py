"""Create dialogue in your own voice with Gemini 3.8 Live and Gemini 3.8 Flash TTS.

This package is the voice core and has no MCP dependency: use it directly from Python
(`VoiceCreator`), from the `voice-creator` CLI, or from Google frameworks such as ADK
and Genkit. The MCP server lives in the separate `voice_creator_mcp` package.
"""

__version__ = "0.2.0"

from voice_creator.client import VoiceCreator

__all__ = ["VoiceCreator", "__version__"]
