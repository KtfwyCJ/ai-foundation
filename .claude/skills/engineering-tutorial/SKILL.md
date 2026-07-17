---
name: engineering-tutorial
description: To summarize, explain, review, or deeply understand a completed module or feature, the objective is to transform an implementation into an engineering tutorial that teaches software architecture, design rationale, production considerations, and Knowledge.
license: MIT
---

# Engineering Tutorial

## Description

Use this skill whenever the user asks to summarize, explain, review, or deeply understand a completed module or feature.

The objective is NOT to summarize code.

The objective is to transform an implementation into an engineering tutorial that teaches software architecture, design rationale, production considerations, and Knowledge.

This tutorial should help a software engineer truly understand WHY the system is designed this way.

The explanation should resemble an internal onboarding document written by a Staff Engineer.

---

# Audience

Assume the reader is:

- Software Engineer
- AI Engineer
- Backend Engineer
- Future AI Platform Engineer

The reader wants to deeply understand the system rather than memorize APIs.

---

# Writing Philosophy

Always teach from first principles.

Never simply explain what the code does.

Instead explain:

- Why it exists.
- Why engineers introduced this layer.
- Why alternatives were rejected.
- What problems this architecture solves.

Always connect implementation to system design.

---

# Required Tutorial Structure

Every tutorial must contain the following sections.

## 1. Executive Summary

What is this module?

Why is it important?

Where does it fit in the platform?

---

## 2. The Problem

What engineering problem does this module solve?

Why couldn't we simply skip it?

What would happen if it didn't exist?

---

## 3. Motivation

Why do enterprise systems introduce this component?

How does the architecture become cleaner because of it?

---

## 4. Responsibilities

Clearly define what this module should do.

Also define what it should NOT do.

Clearly explain module boundaries.

---

## 5. Architecture

Draw a simple architecture diagram.

Explain interactions with upstream and downstream modules.

---

## 6. Request Flow

Walk through a complete request.

Step by step.

Example:

User

↓

Gateway

↓

Provider

↓

OpenAI

↓

Response

---

## 7. Design Decisions

Explain every important design decision.

Examples:

Why Interface?

Why Dependency Injection?

Why Composition?

Why Configuration?

---

## 8. Alternative Designs

Discuss alternative implementations.

Examples:

Direct API Calls

Factory Pattern

Singleton

Service Locator

Compare advantages and disadvantages.

---

## 9. Trade-offs

Explain what is gained.

Explain what becomes more complicated.

Explain engineering compromises.

---

## 10. Production Evolution

Explain how this module usually evolves.

Version 0.1

↓

Version 0.2

↓

Enterprise Version

↓

Large-scale Platform

Discuss scaling challenges.

---

## 11. Real-world Examples

Discuss how similar concepts appear in systems like:

- LiteLLM
- LangGraph
- OpenAI Agents SDK
- Dify
- Langfuse

Only discuss public concepts.

Never speculate about proprietary implementations.

---

## 12. Common Mistakes

List mistakes junior engineers often make.

Explain why they are problematic.

---

## 13. Best Practices

Summarize production best practices.

---

## 14. Knowledge

Assume the reader is preparing for AI Engineer interviews.

Separate into three levels.

### Must Know

Concepts every AI Engineer should know.

### Good to Know

Important engineering improvements.

### Advanced

Topics expected from Senior Engineers.

---

## 15. Key Takeaways

Summarize the five most important ideas.

If the reader remembers only five things, what should they be?

---

# Teaching Style

Act like a Staff Engineer teaching a new teammate.

Never rush.

Always explain concepts before implementation.

Always connect code to architecture.

Always explain trade-offs.

Always use examples.

Use diagrams whenever useful.

---

# Knowledge Depth

Prioritize:

Engineering Thinking

↓

Architecture

↓

Design Decisions

↓

Production Systems

↓

Implementation Details

Code is the least important part.

Understanding WHY is the most important.

---

# Completion

At the end of every tutorial include:

## Further Reading

Recommend:

- Papers
- GitHub repositories
- Official documentation
- Blog posts

Rank them by importance.

---

## Next Module

Recommend what the reader should study next in order to naturally build a complete AI Platform understanding.


# Output

Every tutorial MUST be written into:

/engineer-tutorial/

The filename should follow:

01-gateway.md

02-provider-layer.md

03-runtime.md

...

If the file already exists:

Update it instead of rewriting from scratch.

Keep previous content whenever appropriate.

Always improve the document incrementally.

Never overwrite useful information.