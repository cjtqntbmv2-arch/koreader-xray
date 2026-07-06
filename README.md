# X-Ray Plugin for KOReader

![version](https://img.shields.io/badge/version-26.7.4-blue)
![Platform](https://img.shields.io/badge/platform-KOReader-green.svg)
![License](https://img.shields.io/badge/license-MIT-yellow.svg)

This plugin brings Kindle-style X-Ray features to KOReader. It uses AI to track characters, build plot timelines, and provide insights while you read.

## What it does

- **AI-Powered Insights**: Supports Google Gemini, OpenAI, **DeepSeek**, **Claude**, and **Custom API** providers (like OpenRouter).
- **Character Tracking**: View bios and roles. Now supports **Merging Duplicates** with AI-consolidated summaries.
- **Customizable Detail**: Choose between short or long AI descriptions to fit your preference.
- **Linked Entries**: Automatically connect related characters and locations through smart cross-referencing.
- **Plot Timeline**: Keeps track of major events chapter by chapter, strictly sorted by physical page location for accuracy.
- **Historical Context**: Pulls real-world info for historical figures and locations.
- **Mention Scanning**: Find every occurrence of a character or location throughout the book, complete with page numbers and context snippets for quick navigation.
- **Spoiler Protection**: "Spoiler-free" mode only reads up to your current page so future twists aren't ruined.
- **Auto Fetching while you read**: Automatically fetches data in the background when you get to a new chapter.
- **X-Ray Mode & Inline Fetching**: Get instant lookups by tapping the "X-Ray" button in dictionary or selection popups. If an entity is missing, the plugin can fetch it on-the-fly using AI without requiring a full book scan.
- **Silent Weekly Updates**: Automatically checks for new plugin versions in the background once a week.
- **Offline First**: You only need internet to fetch the data. After that, it's saved locally.
- **Multilingual**: Available in English, Arabic, Dutch, French, German, Hungarian, Indonesian, Italian, Polish, Brazilian Portuguese, Russian, Serbian, Simplified Chinese, Spanish, Turkish, and Ukrainian.

## Installation

1. Download `xray.koplugin.zip` from the [latest release](https://github.com/cjtqntbmv2-arch/koreader-xray/releases/latest).
2. Extract it into KOReader's `plugins/` folder, so you end up with `plugins/xray.koplugin/`.
3. Restart KOReader.

## Setup

Open a book and pick X-Ray from the reader menu. On first use the plugin walks you through storing an API key for your preferred AI provider (Gemini, OpenAI, DeepSeek, Claude, or a custom OpenAI/Anthropic-compatible endpoint). Keys are stored on-device in `xray_config.lua` and survive plugin updates.

## Credits

Based on [koreader-xray-plugin](https://github.com/ultimatejimmy/koreader-xray-plugin) by Jimmy Pautz (MIT).
