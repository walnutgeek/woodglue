# MillFarm — Terminology \& Design Reference


Summary of [ppxty thread](https://www.perplexity.ai/search/can-you-come-up-with-another-n-iHkbM6rAT5SO.VLYOQ3K_w)

## Overview

MillFarm is a distributed task processing system built around a **wood mill metaphor**. Every component of the system is named after its real-world analog in a lumber operation. The metaphor is intentional: it reflects the nature of the work — raw material comes in, gets processed, and finished product comes out — with a foreman optimizing throughput across a farm of mills.

***

## Core Terms

### Timber

A **unit of work** — a single task submitted to the system for processing.

The name reflects that just like a physical timber (a raw log), you can look at it and get a rough sense of how much work it represents before you start cutting. It is the raw material of the system.

***

### Timber Weight

A **scalar estimate of a task's total cost**, folding CPU, memory, and IO demands into a single abstract number.

The key insight behind the name: a physical timber can be **small but dense** — you cut through it more slowly than its size suggests. Similarly, a task can be small in input size but expensive in execution. **Weight** captures this multi-dimensional nature better than "size" would. You can't know the exact proportions of CPU vs. memory vs. IO, but weight gives you a meaningful single number that *correlates* with total resource consumption over the task's lifetime.

***

### Mill

A **worker instance** capable of processing multiple timbers concurrently.

Like a physical sawmill, a Mill can handle more than one timber at a time. It is the basic unit of processing capacity in the system. Multiple Mills form a MillFarm.

***

### Max Active Weight

The **dynamic capacity ceiling** of a single Mill — the maximum total timber weight it can carry simultaneously without degrading throughput.

This value is **not known ahead of time**. It is amorphous and depends on the actual hardware, runtime environment, and nature of the timbers being processed. The Foreman starts from a conservative estimate and adjusts upward dynamically, using throughput as the feedback signal. It represents how much "weight" a mill can bear at once before it starts slowing down.

***

### MillFarm

The **cluster of all Mills** operating together as a unified processing system.

Like a lumber yard housing multiple sawmills, the MillFarm is the top-level collective. Jobs flow into the MillFarm; the Foreman manages how they are distributed across individual Mills. Total system throughput is measured at the MillFarm level.

***

### Foreman

The **orchestrator instance** responsible for dispatching timbers to mills and maximizing MillFarm throughput.

The Foreman's job mirrors its real-world namesake: it watches the floor, knows the capacity of each Mill, and routes incoming timbers to keep every Mill busy without overloading any of them. It is the brain of the MillFarm.

***

## Foreman's Algorithm

The Foreman operates a **dynamic max active weight tuning loop**:

1. **Start conservative** — begin with a low max active weight per Mill to avoid overload.
2. **Increase gradually** — push more timber weight through each Mill while monitoring throughput.
3. **Detect diminishing returns** — when throughput stops improving (or degrades), the current weight ceiling is too high.
4. **Scale back and hold steady** — reduce to the last stable ceiling and maintain it for the remainder of the Timber queue.
5. **Goal** — process the entire Timber queue in the **fastest possible total time**, not just maximize instantaneous concurrency.

This means the Foreman is not just a dispatcher — it is an **active throughput optimizer** that learns the MillFarm's real capacity at runtime.

***

## Relationships at a Glance

| Term | Analog | Role |
| :-- | :-- | :-- |
| Timber | Raw log | Unit of work / task |
| Timber Weight | Log density | Multi-dimensional cost estimate |
| Mill | Sawmill machine | Worker / processing instance |
| Max Active Weight | Mill load limit | Dynamic capacity ceiling per Mill |
| MillFarm | Lumber yard | Cluster of all Mills |
| Foreman | Floor supervisor | Orchestrator / scheduler / optimizer |


---
