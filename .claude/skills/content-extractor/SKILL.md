---
name: content-extractor
description: Extracts and analyzes content from URLs (YouTube videos, blog posts, web pages). Use when the user provides a URL and wants to extract, summarize, compare, or analyze content from it.
---

# Content Extraction and Analysis

## URL Detection and Tool Routing

When the user provides a URL, detect the type and call the appropriate MCP tool:

### YouTube Videos
**Detect**: URL contains `youtube.com/watch`, `youtu.be/`, or `youtube.com/embed/`
**Tool**: `extract_youtube_transcript`
**Example call**:
```
extract_youtube_transcript(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
```

### Blog Posts / Web Pages
**Detect**: Any other HTTP/HTTPS URL
**Tool**: `extract_blog_content`
**Example call**:
```
extract_blog_content(url="https://blog.hubspot.com/marketing/content-strategy")
```

## Analysis Patterns

After extracting content, apply the appropriate analysis based on the user's request:

### Summarize
- Provide a concise summary (3-5 bullet points for short content, structured sections for long content)
- For YouTube: include video duration and whether captions are auto-generated
- For blogs: include word count and reading time

### Extract Key Points
- Pull out main arguments, data points, and actionable takeaways
- For YouTube: reference timestamps when citing specific points
- For blogs: note the author and publication context

### Compare Content
- When given multiple URLs, extract all content first, then compare
- Highlight agreements, contradictions, and unique points from each source
- Organize comparison by theme, not by source

### Q&A / Deep Analysis
- Use the full extracted content to answer specific user questions
- Cite relevant sections (with timestamps for video, with quotes for text)
- Flag when the content does not cover the user's question

## Error Handling

- If a YouTube transcript is unavailable, inform the user and suggest checking if the video has captions enabled
- If a blog page is blocked, suggest the user try a different URL or mention the page may have anti-bot protection
- If the URL format is not recognized, ask the user to confirm the URL is correct

## Multiple URL Workflow

When the user provides multiple URLs:
1. Extract content from each URL (call tools sequentially)
2. Present results organized by source
3. If the user asks for comparison, synthesize across all sources
