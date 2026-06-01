# Disclaimer, trademarks, and privacy

STELLA is an unofficial, non-commercial hobby project, provided "as is" with no
warranty (see [LICENSE](LICENSE)). Use it at your own risk.

## Not affiliated; trademarks belong to their owners

This project is not affiliated with, endorsed by, or sponsored by any of the
companies or brands it interoperates with. All product names, trademarks, and
registered trademarks are the property of their respective owners, used here only
for identification and interoperability:

- **Star Citizen**, the ship/keybind names, and related marks are the property of
  **Cloud Imperium Games / Roberts Space Industries (CIG/RSI)**.
- **"Cortana"** and any other assistant or character names are the property of
  **Microsoft Corporation** (and/or other rights holders). STELLA ships no Cortana
  voice, model, or audio. Any resemblance you choose to create with your own
  reference audio is your responsibility, not this project's.
- **Chatterbox** is a TTS engine by **Resemble AI**; **Piper** is by the Rhasspy
  project; **Whisper** is by **OpenAI**; **Ollama** and the language models are the
  property of their respective owners.

## Voice cloning

This repository contains **no trained voice and no reference audio**. The optional
Chatterbox service (see [voice/](voice/)) can imitate a voice only if you supply
your own reference clip. You are solely responsible for:

- having the legal right to use any reference audio you provide, and
- not using a cloned voice to impersonate a real person, mislead others, or for any
  commercial purpose.

Recreating a real person's or a trademarked character's voice can infringe
intellectual property and publicity/privacy rights. Keep anything you generate to
personal use.

## Anti-cheat / game integrity

STELLA presses keys via synthetic input (the same SendInput approach community
tools like VoiceAttack use, which players have run under Easy Anti-Cheat). That has
worked in practice, but anti-cheat behavior can change at any time and is entirely
outside this project's control. You are responsible for complying with the game's
EULA and rules of conduct. Test in a safe situation before relying on it.

## Privacy and data

- **Local by default.** Speech-to-text, the local LLM (Ollama), and Piper TTS all
  run on your own machine. Nothing is sent to the cloud unless you opt in.
- **Opt-in external services.** If you configure an external LLM provider
  (OpenAI/Anthropic/etc.) your transcribed text is sent there under their terms. The
  optional knowledge feature queries the StarCitizenWiki and UEX Corp APIs; the
  optional UEX features require a token you create. Those are third-party services
  with their own terms and privacy policies.
- **Secrets stay local.** API tokens live in a local `.env` (gitignored) and are
  never committed.

Game data shown by the knowledge feature comes from community sources
(StarCitizenWiki, UEX Corp) and remains subject to their terms and accuracy.
