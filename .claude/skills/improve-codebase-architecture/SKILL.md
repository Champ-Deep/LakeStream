---
name: improve-codebase-architecture
description: Surface architectural friction and propose deepening opportunities — refactors that convert shallow modules into deep ones for better testability and AI-navigability. Use when the user asks about improving architecture, refactoring modules, reducing coupling, identifying god classes, finding seams, or making the codebase easier to navigate and test. Also trigger when the user asks "what should we improve", "what's wrong with the architecture", or "where is the complexity hiding".
---

# Improve Codebase Architecture — Skill Overview

This skill helps teams surface architectural friction and propose **deepening opportunities**—refactors that convert shallow modules into deep ones for better testability and AI-navigability.

## Core Approach

The skill uses a consistent glossary (Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality) to avoid terminology drift. It applies the **deletion test**: if removing a module concentrates complexity rather than merely displacing it, that module earned its weight.

## Three-Phase Process

**1. Explore organically** — Read domain glossary and ADRs first. Use the Agent tool to walk the codebase, noting friction points: concepts requiring bouncing between many small modules, shallow interfaces, extracted pure functions hiding real bugs, or tightly-coupled leaks across seams.

**2. Present candidates** — List deepening opportunities with Files, Problem, Solution, and Benefits. Use domain vocabulary from CONTEXT.md and architectural terms from LANGUAGE.md. Flag any ADR conflicts only when friction is real enough to warrant reopening.

**3. Grill loop** — Once the user selects a candidate, walk the design tree together. Update CONTEXT.md if naming new concepts, sharpen fuzzy terms inline, and offer ADRs when rejection reasons would guide future reviews.

The skill never proposes final interfaces upfront—it asks which candidate to explore first.
