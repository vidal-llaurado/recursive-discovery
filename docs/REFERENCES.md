# References and intellectual lineage

[README](../README.md) · [Formalism](FORMALISM.md)

These references identify the ideas that shaped Recursive Discovery. Each entry separates what this project adopted from what it did not implement.

## 1. Markus J. Buehler — Recursive Meta-Intelligence

**Markus J. Buehler. *Recursive Meta-Intelligence*. 2026.**  
[Original article](https://x.com/ProfBuehlerMIT/article/2099834306046664792)

The original article, supplied during this project's design, describes representations becoming instruments, instruments becoming executable worlds, and persistent abstractions supporting later reasoning.

**Adopted:** research products should remain usable outside a single context window; instruments, representations, and concepts can become inputs to subsequent investigation.

**Adapted:** Recursive Discovery gives mathematical checks and empirical protocols separate execution paths. Its artifact graph is a shared workspace rather than a reproduction of the article's materials simulator or population-scale exploration.

## 2. Pal, Wang, and Buehler — SwarmWorld

**Subhadeep Pal, Fiona Y. Wang, and Markus J. Buehler. *SwarmWorld: Stigmergic technological evolution in societies of language-model agents*. 2026.**  
[arXiv:2608.26081](https://arxiv.org/abs/2608.26081)

SwarmWorld describes agents constructing persistent technologies whose function is determined by a simulator rather than by the agents' claims. Reuse and specialization occur through the shared environment.

**Adopted:** persistent artifacts as a common habitat, and a distinction between proposing an object and observing its consequence.

**Not reproduced:** decentralized population dynamics, physical stigmergy, or the reported technological ecology. Recursive Discovery currently supplies a predominantly serial model-owned runtime with persistent branches, and its execution signatures record provenance rather than substituting for SwarmWorld's functional evaluator.

## 3. Zheng et al. — Dream-RSI

**Tong Zheng et al. *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*. 2026.**  
[arXiv:2609.14858](https://arxiv.org/abs/2609.14858) · [Project](https://dream-rsi.com/)

Dream-RSI uses realized discovery trees to evaluate alternative exploration policies by replay, then redeploys an improved policy to collect further history:

$$
\mathcal H_t\xrightarrow{\text{replay}}\pi_{t+1}
\xrightarrow{\text{deploy}}\mathcal T_{t+1}.
$$

**Adopted:** explicit decision/outcome history. Its reported semantic-guidance ablation also prompted a preference for raw outcomes over memo summaries in context ranking, which is a design choice rather than a general result about memory.

**Not reproduced:** policy optimization or its replay simulator. Recursive Discovery records state digests, selected payload summaries, artifact references, and outcomes. Current lookup indexes recorded outcomes rather than reconstructing a counterfactual environment.

## Attribution and scope

The resulting design combines persistent research worlds, separate mathematical/empirical records, prospective evaluation interfaces, reusable instruments, compositional concepts, and decision history. Its claims rest on its own implementation and deployments rather than on the performance or scale of the systems above.

The architectural references above remain the intellectual lineage for this work. This repository does not bundle the demonstrations or benchmarks of the cited systems.
