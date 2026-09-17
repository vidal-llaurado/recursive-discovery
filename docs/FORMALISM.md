# Mathematical specification

[README](../README.md) · [Design](DESIGN.md) · [Usage](USAGE.md) · [References](REFERENCES.md)

This specification describes Recursive Discovery `v1.0.0`. Each operator's research interpretation is stated alongside the contract the software actually implements, so the two can be checked against each other.

## State and notation

| Symbol | Meaning |
| :--- | :--- |
| $a$ | An artifact with a kind, payload, references, author label, and creation time. |
| $D_t=(\mathcal A_t,\mathcal R_t)$ | The persistent artifact graph and its typed references. |
| $\mathcal E_t\subseteq\mathcal A_t$ | Evidence artifacts with a valid kernel authentication tag. |
| $\Phi_t$ | Callable instrument definitions available to research. |
| $G_t$ | A compositional grammar, including learned macros. |
| $\mathcal H_t$ | Decision records and linked realized outcomes. |
| $W_t=(D_t,\Phi_t,G_t,\mathcal H_t)$ | The research world: a logical view over these persistent records. |
| $\tau_t$ | A task selected from the current frontier. |
| $C_t$ | The context packet compiled for that task. |
| $j_t,r_t,e_t$ | A declared execution job, its result record, and its signed evidence artifact. |
| $b_t$ | An abstraction proposed for reuse in a subsequent world. |

The components of $W_t$ are not disjoint databases. Instruments, grammar entries, and history are themselves represented through artifacts and references.

An artifact's identifier is content-derived:

$$
\operatorname{id}(a)=\operatorname{digest}(\text{kind},\text{data},\text{references},\text{author}).
$$

`core.py` uses a truncated SHA-256 digest for artifact IDs. `BlobStore` uses full SHA-256 digests for retained bytes. Creation time is not part of artifact identity. The ledger's public write operation appends or returns an existing content-identical record.

## Kernel: identity, execution, and authority

### 2.1 The declared job

Conceptually, an execution request is

$$
j=(\text{target},\text{tool},\text{argv},\text{declared inputs},\text{environment},\text{seed},\text{limits}).
$$

`Kernel.use(...)` formats a tool descriptor into such a request. `Kernel.run(...)` executes locally; `WorkerKernel` delegates execution to a worker. The record contains, among other fields,

$$
r=(\text{exit status},\text{stdout},\text{stderr},\text{elapsed time},\text{run metadata}).
$$

The base implementation hashes declared inputs, records runtime settings, and retains bounded stdout/stderr. It does not infer undeclared dependencies.

### 2.2 Authentication, not a theorem

Let $k$ be the kernel's signing key. In schematic form,

$$
\sigma=\operatorname{HMAC}_k(\text{kind},r,\text{references},\text{author}),\qquad
 e=(r,\sigma).
$$

`Kernel.verify(e)` checks this tag. The scientific distinction is

$$
\operatorname{Verify}_k(e)=\text{true}
\ \not\Rightarrow\
\text{the associated scientific claim is true}.
$$

The tag authenticates a record under a shared secret. It does not independently validate the tool's semantics, make a Python assertion a formal proof, establish that the formalization matches the intended claim, or turn a benchmark score into a universal conclusion.

### 2.3 Authority labels

`context.authority(...)` produces:

| Label | Implemented meaning |
| :--- | :--- |
| `proposal_or_state` | A hypothesis, memo, world, or other ordinary record. |
| `retrieved_source` | A source, source text, or retrieval record. |
| `consequence` | An evidence artifact whose kernel tag verifies. |
| `derived_from_consequence` | A supported derived artifact type with reference paths to signed evidence. |
| `untrusted` | An evidence artifact with no valid tag. |

The derivation label describes **provenance linkage** rather than an independent recomputation of the measurement or summary. A proposed claim does not change type because its own payload says `pass`.

### 2.4 Eligibility and promotion

The frontier is a rule-derived set:

$$
\mathcal F(D_t)=\{\tau:\text{a routing obligation is not yet satisfied in }D_t\}.
$$

For example, a claim without signed mathematical execution evidence creates `verify`; an experiment without empirical execution evidence creates `run`. The implementation uses the presence and reported verdict of linked records to route work. It is not an exhaustive scientific validity checker.

`compress(...)` records an invariant from a bridge labeled `agreement`. `promote(...)` accepts an invariant, representation, or mechanism and records a world referencing it:

$$
W_{t+1}=W_t\oplus b_t.
$$

Here $\oplus$ denotes a proposed reusable basis and its lineage. These functions do not themselves rerun validation. Evidence-sensitive acceptance therefore also depends on the calling policy, checker configuration, and deployment boundary.

## Research, context, and the two streams

### 3.1 Model-owned selection

The researcher selects a task and then acts within a task-specific context:

$$
\tau_t\sim\pi_\theta(\cdot\mid\mathcal F(D_t),W_t),\qquad
C_t=\mathcal C(W_t,\tau_t;B),\qquad
 a_t\sim\pi_\theta(\cdot\mid C_t).
$$

A session can inspect state, search sources, use instruments, write memos, and propose candidate sets $P_t=\{p_1,\ldots,p_m\}$. Candidate activation records selected proposals; it does not validate them.

$B$ is a context-capacity setting. The compiler uses graph proximity, lexical hits, artifact-kind weights, authority labels, and clipping. Token estimates are approximate, and the session also supplies tool descriptions and recent observations.

The model-owned runtime selects work subjectively. Older `bid`, `allocate`, and `calibration` helpers remain optional policies.

### 3.2 Mathematical execution

A formalization $\tilde p$ is submitted to a chosen checker:

$$
r^M=\operatorname{Exec}_M(\tilde p),\qquad e^M=K(j^M,r^M).
$$

Installed backends may include Lean, SMT solvers, symbolic checks, and executable Python checks. These have different semantics. A returned result must be interpreted in its formalism and under its assumptions; a failed process is not automatically a counterexample.

### 3.3 Empirical execution

An experimental protocol $\mathcal P$ maps data, initialization, hyperparameters, and environment to measured output:

$$
Y=\mathcal P(D,S,\Lambda,E).
$$

Repeated runs yield $Y_1,\ldots,Y_n$ and a derived summary $\mathcal S(Y_1,\ldots,Y_n)$. Source preservation and signed execution are distinct from analysis:

$$
\text{execution record}\ne\text{reported measurement}\ne\text{scientific conclusion}.
$$

Summary functions provide estimates, dispersion, intervals, quantiles, and contrasts. Their statistical interpretation depends on sampling, dependence, and stopping assumptions. Formal and empirical evidence are never combined into a single confidence scalar.

## Scientific transformations

These operators primarily live in [`ops.py`](../src/recursive_discovery/ops.py). They are replaceable scientific policies, not additional powers of the signing kernel.

### 4.1 Assumption → intervention

For a scalar predicate $x\mathbin{\triangleleft}c$, `stress(...)` proposes a small boundary-crossing set, schematically

$$
\mathcal I(A)=\{c-\epsilon,c,c+\epsilon\}.
$$

The ordering depends on the comparison operator. This is a finite intervention proposal for one scalar predicate, not a general method for testing arbitrary mathematical assumptions.

### 4.2 Discrepancy → localized question

For prediction $\hat y_i$, observation $y_i$, and supplied assumption flags $A_{ij}$,

$$
\delta_i=\mathbf 1[d(\hat y_i,y_i)>\text{tolerance}],\qquad
s_i=\bigwedge_j A_{ij}.
$$

The diagnosis distinguishes mismatch inside and outside declared scope:

$$
\delta_i=1,\ s_i=0\Rightarrow\text{investigate scope},\qquad
\delta_i=1,\ s_i=1\Rightarrow\text{investigate an in-scope discrepancy}.
$$

It may summarize the observed association of an assumption with mismatch:

$$
L(A)=\widehat P(\delta=1\mid\neg A)-\widehat P(\delta=1\mid A).
$$

This is descriptive, not causal. `diagnose(...)` uses caller-supplied flags; it does not independently verify the assumptions. In-scope disagreement can also arise from a bad formalization, implementation defect, numerical error, or measurement error—not only from a missing theory. Missing assumption flags require care: the current operator treats absent flags as false.

### 4.3 Observations → competing conjectures

The initial conjecture operator searches simple one-feature rules. For a rule $h$,

$$
\widehat{\operatorname{accuracy}}(h)=\frac1n\sum_i\mathbf1[h(x_i)=y_i].
$$

The best rule per feature can remain available for discrimination. Agreement on observed cases is not proof or evidence of a causal mechanism.

### 4.4 Competing explanations → discriminating experiment

Let $H$ be a random variable over live deterministic hypotheses with supplied weights. Under a fixed candidate intervention $u$, each hypothesis predicts $Y_u=f(H,u)$. Then

$$
\operatorname{IG}(u)=\mathrm I(H;Y_u)=\mathsf H(Y_u),
$$

because $\mathsf H(Y_u\mid H)=0$. The supplied heuristic selects

$$
u^*\in\arg\max_u\frac{\operatorname{IG}(u)}{c(u)}.
$$

Here $c(u)$ is a declared experimental burden. This local heuristic is separate from model-owned task selection. It assumes deterministic predictions and a fixed candidate set; noisy likelihoods require a different policy.

After observation $y$, resolution retains

$$
\mathcal V' = \{h\in\mathcal V:d(f(h,u),y)\leq\epsilon\}.
$$

An empty survivor set is recorded as surprise. It means the supplied hypotheses did not predict this observation within tolerance, not that every possible explanation has been falsified.

### 4.5 Surprise → representation or mechanism proposal

On a finite case set $S$, the current diagnostic computes empirical conditional entropy using exact coordinate matches:

$$
\widehat U_S(C)=\widehat{\mathsf H}_S(Y\mid C),\qquad
\widehat{\Delta U}_S(z)=\widehat U_S(C)-\widehat U_S(C,z).
$$

Positive empirical entropy detects outcome variation within represented states. An observable that reduces it is a candidate refinement. Zero entropy merely means the supplied cases show no such collisions; with unique continuous coordinates it can occur trivially. Neither outcome proves representation sufficiency or establishes a missing causal variable. Noise can also produce conditional variation.

`expand_representation(...)` uses this limited diagnostic to return `expanded`, `blind`, or `mechanism`. These are routing suggestions on the supplied data.

### 4.6 Instrument and mechanism synthesis

An instrument is a transformation $\phi:X\to Z$. The search policy can rank a candidate by empirical ambiguity reduction relative to measurement and description cost:

$$
q(\phi)=\frac{\widehat U_S(C)-\widehat U_S(C,\phi(X))}
 {c_{\mathrm{measure}}(\phi)+c_{\mathrm{description}}(\phi)}.
$$

Mechanism search uses a small expression grammar and an affine readout:

$$
\hat Y=a\,\phi(C)+b.
$$

Candidates are fitted and ranked by error and a simplicity tie-break. This is a bounded synthesis policy, not a universal scientific learner. Prospective checker results and supplied data determine what the surrounding policy accepts.

### 4.7 Persistent workbench instruments

The interactive workbench has a separate lightweight definition path. A model declares inputs, an expression, and interface cases. After those cases pass, the definition is available to future workbench instances:

$$
\Phi_{t+1}=\Phi_t\cup\{\phi\}.
$$

A callable definition has passed only the supplied interface cases. It has not been proved correct over its entire domain or shown scientifically useful. The expression interpreter has a fixed function vocabulary; this path is not an unrestricted language-extension or arbitrary-code facility.

### 4.8 Grammar recursion

The synthesis grammar can name a useful composite $m_n$:

$$
G_{n+1}=G_n\cup\{m_n\}.
$$

This turns a deeper expression into a shallow symbol for later search. Search can retain only one representative of a task-local behavior signature:

$$
p\sim_S q\iff p(x_i)=q(x_i)\quad\text{for all }x_i\in S.
$$

Finite-sample equivalence is not mathematical equivalence. A future case can distinguish two programs that were indistinguishable on $S$. The grammar helpers and the interactive expression workbench are related mechanisms, not one automatically unified registry.

## History and prospective evaluation

### 5.1 Decision records

Before an action is executed, record

$$
d_j=(h_j,a_j,p_j,v_j,\pi_j,\operatorname{parent}_j),\qquad
h_j=\operatorname{hash}(s_j).
$$

$p_j$ summarizes the action payload; $v_j$ identifies visible artifacts and action/tool choices. A later `decision_outcome` references $d_j$ and its observed or produced artifacts.

$$
\mathcal H_t=\{(d_j,o_j)\}_{j\leq t}.
$$

The current lookup is an index:

$$
\operatorname{Lookup}(\mathcal H,h,a)
 =\{o_j:h_j=h\ \land\ \operatorname{name}(a_j)=a\}.
$$

It can return several records. It matches the stored state digest and action name, not a full regenerated world or independently matched action payload. A caller must inspect payloads and provenance to distinguish those outcomes. The module stores state digests and selected references, not a lossless full context snapshot. It is therefore a basis for later replay work—not an exact general counterfactual simulator or a learned exploration-policy optimizer.

### 5.2 Prospective commitments

A commitment links a proposal and test handle with predictions, a metric, and a decision rule:

$$
\kappa=\operatorname{id}(p,\text{test handle},\hat Y,\mu,\rho).
$$

The intended ordering is

$$
t_{\mathrm{commit}}<t_{\mathrm{observe}}.
$$

The local vault and the evaluator-service adapter support one-use tests and retained evaluation records. Their security depends on actual separation of files, processes, keys, and credentials. Recording a commitment does not prevent leakage through another access path, and a publication-date filter does not remove knowledge already in a model.

## Source map and interpretation

| Component | Main source |
| :--- | :--- |
| Artifacts, signing, routing, promotion | [`core.py`](../src/recursive_discovery/core.py) |
| Durable graph and files | [`store.py`](../src/recursive_discovery/store.py) |
| Model-owned sessions | [`session.py`](../src/recursive_discovery/session.py), [`runtime.py`](../src/recursive_discovery/runtime.py) |
| Context and authority labels | [`context.py`](../src/recursive_discovery/context.py) |
| Scientific policy operators | [`ops.py`](../src/recursive_discovery/ops.py) |
| Instruments and definitions | [`instruments.py`](../src/recursive_discovery/instruments.py), [`dynamic_instrument.py`](../src/recursive_discovery/dynamic_instrument.py) |
| Execution and measurements | [`worker.py`](../src/recursive_discovery/worker.py), [`lab.py`](../src/recursive_discovery/lab.py), [`stats.py`](../src/recursive_discovery/stats.py) |
| Decision history | [`replay.py`](../src/recursive_discovery/replay.py) |
| Prospective evaluation | [`sealed.py`](../src/recursive_discovery/sealed.py), [`sealed_service.py`](../src/recursive_discovery/sealed_service.py) |

The architectural intent is to let investigation become more capable without confusing model judgment with recorded consequence. The implementation supplies those records and interfaces; sound science and secure deployment still require appropriately chosen checkers, protocols, and access boundaries.
