## Duplicate adjudication - performance evaluation

> **Working draft for incorporation into a Methods section.** This text describes the proposed performane evaluation of the duplicate adjudication method.

### General design and outcome definitions

Performance evaluation will be conducted only after the duplicate adjudication method has been finalized and its source code, configurable thresholds, large language model prompts and deployment, database snapshot, and analysis procedures have been frozen. The test data will not be accessed by the co-authors responsible for method development before this freeze. Report-level large language model extractions generated for the evaluation will likewise be frozen before pair-level performance analyses are undertaken.

Human classifications of *Duplicate* will be treated as positive-control labels and classifications of *Non-duplicate* as negative-control labels. Pairs for which the reference annotator abstained will be excluded from the primary evaluation.

For the primary binary evaluation of the duplicate adjudicator, an output of *Duplicates* will be treated as a positive classification, whereas *Otherwise related*, *Unrelated*, and *Abstain* will be treated as negative classifications. A prespecified secondary analysis will instead treat adjudicator abstentions as positive classifications.

Precision will be calculated as \(TP/(TP+FP)\), and recall as \(TP/(TP+FN)\). Results will be presented as counts and proportions with two-sided 95% confidence intervals. Recall will primarily be reported separately for each reference set rather, because the reference sets differ in their provenance and annotation procedures.

### Precision evaluation

The primary aim of the precision evaluation will be to assess whether the duplicate adjudicator can reliably remove false-positive suspected duplicates from the output of vigiMatch2025 while retaining true duplicates.

The evaluation set will comprise 50 report pairs identified as suspected duplicates by vigiMatch2025 in VigiBase up until January 28, 2026. Some suspected duplicates form larger clusters, and to avoid any over-representation, random sampling was performed by first randomly selecting clusters and then randomly sampling one report pair per cluster. The pairs were independently labelled in March 2026 by one co-author as *Duplicate*, *Non-duplicate*, or *Abstain*. These labels and the underlying report pairs were not viewed by the co-authors responsible for developing the duplicate adjudication method before it and its parameters were frozen. Pairs assigned *Abstain* by the reference annotator will be excluded.

Because every pair in this evaluation set was classified as a suspected duplicate by vigiMatch2025, the proportion labelled as duplicates by the human annotator will represent the precision of the unmodified vigiMatch2025 candidate set within this sample. The duplicate adjudicator will then be applied to the same pairs. Its performance will be summarized using:

- precision among pairs classified as *Duplicates* by the adjudicator;
- conditional recall, defined as the proportion of reference duplicates retained as *Duplicates* by the adjudicator;
- the proportion of vigiMatch2025 false positives correctly rejected by the adjudicator;
- the overall proportion of vigiMatch2025 candidates removed; and
- the number and proportion of adjudicator abstentions.

A complete cross-classification of the adjudicator output against the original human annotation will be presented.

### Recall evaluation

The primary aim of the recall evaluation will be to quantify any loss of recall when the duplicate adjudicator is used to post-process report pairs identified as suspected duplicates by vigiMatch2025. A secondary aim will be to estimate the adjudicator’s maximum attainable recall if it were supplied with candidates from a screening method with complete or near-complete coverage of the reference duplicates.

The recall evaluation will use held-out test portions of five regulator-derived reference sets previously used to evaluate vigiMatch2025: FDA Gold, FDA Silver, the Spanish AEMPS reference set, the UK Medicines and Healthcare products Regulatory Agency reference set, and the Netherlands Pharmacovigilance Centre Lareb reference set. Only portions reserved for final testing and not accessed during development of vigiMatch 2025 or the duplicate adjudication method will be eligible. For each reference set, up to 100 duplicate pairs will be randomly sampled, with no more than one sampled pair from the same duplicate cluster. Clusters rather than individual pairs will first be sampled, after which one eligible pair will be selected from each sampled cluster. If fewer than 100 eligible clusters are available, all eligible clusters will be included. The randomization procedure and seed will be recorded.

FDA Gold was based on duplicate groups reviewed by at least two FDA safety reviewers with adjudication when required, whereas FDA Silver was validated by a single reviewer. 

For the Spanish and Dutch reference sets, which include both manually classified duplicates and pairs linked through E2B fields, sampling will prioritize manually classified pairs. E2B-derived pairs will be added only when required to reach the target sample size.

The MHRA reference set, ...

Two recall estimands will be calculated for each reference set:

1. **Recall of the serial vigiMatch2025–adjudicator solution.** A reference duplicate will be considered retrieved only when it is first identified as a suspected duplicate by vigiMatch2025 and subsequently classified as *Duplicates* by the adjudicator.
2. **Recall of the adjudicator under complete candidate coverage.** The adjudicator will be applied directly to every sampled reference pair, irrespective of whether vigiMatch2025 identified it. This estimates the adjudicator’s recall ceiling under a hypothetical upstream screening process that presents all reference duplicates for adjudication; it does not by itself estimate the performance of any particular lowered-threshold screening method.

For comparison, the recall of vigiMatch2025 alone will also be calculated. The joint classifications of vigiMatch2025 and the adjudicator will be tabulated as:

- retrieved by both methods;
- retrieved by neither method;
- retrieved by vigiMatch2025 only; and
- retrieved by the adjudicator only.

The reduction in recall attributable to post-processing will be calculated as the recall of vigiMatch2025 alone minus that of the serial solution. The difference between the adjudicator’s complete-coverage recall and vigiMatch2025 recall will be reported descriptively, together with the joint classification table. Since all cases in the recall reference sets are positive controls, these analyses will not estimate precision, specificity, or false-positive rates.

### Structured case-level examination

Structured case-level examination will complement the aggregate performance estimates by examining representative correct classifications, misclassifications, and disagreements between methods. SCLE is intended to contextualize statistical performance rather than replace it, and the published framework recommends stratified examination of errors and correct positive classifications, with oversampling of cases on which a new method and its comparator disagree. 

Because the objective of the SCLE is diagnostic examination of method behaviour rather than independent reclassification, reviewers will be provided with the reference classification, the method output, and the corresponding confusion-matrix category. This enables the examination to be tailored to correct and incorrect classifications but does introduce a risk of confirmation bias. This risk is mitigated through a predefined assessment framework, independent examination by two reviewers, and subsequent arbitration.

For the precision evaluation, a random sample of up to ten pairs will be selected from each available cell of the adjudicator-versus-reference confusion matrix: false positives, false negatives, true positives, and true negatives. If a cell contains fewer than ten pairs, all pairs in that cell will be reviewed. Although true negatives are often of limited interest in rare-event recognition generally, they are relevant here because they represent successful removal of false-positive vigiMatch2025 candidates.

For each recall reference set, up to five distinct pairs will be sampled from each of the following joint vigiMatch2025–adjudicator strata:

1. retrieved by both methods;
2. retrieved by neither method;
3. retrieved by vigiMatch2025 only; and
4. retrieved by the adjudicator only.

This will yield up to 20 unique SCLE cases per reference set. If a stratum contains fewer than five eligible pairs, all available pairs will be included. Sampling by joint strata avoids counting the same disagreement pair separately as a true positive for one method and a false negative for the other.

Two co-authors will independently examine each selected pair. Disagreements between the two reviewers will be resolved with the participation of the co-author who produced the original reference annotation, acting as an arbiter.

The SCLE form will include a concise free-text case assessment together with the following structured dimensions, adapted from the published SCLE framework:

- **Case difficulty (triviality):** considering the information available to the method, whether correct binary classification of the pair would be trivial, clear, non-trivial, or unclear. Because duplicate identification is inherently ambiguous, *trivial* is expected to be very rare and is reserved for pairs where nothing is left to judge — the reports match exactly, carry sufficient information, and are explicitly linked to one another. *Clear* is used where the correct label is clear to a human reviewer but arriving at it is not trivial for a computational method, which would still have to extract that information reliably; two reports whose narratives plainly describe the same case belong here. *Non-trivial* is used for pairs that are genuinely difficult to classify, and *unclear* only for pairs so difficult that the reviewer would have abstained;
- **Reference-label issue:** annotated as no apparent issue, possibly incorrect reference label, or genuinely ambiguous case;
- **Never event:** for misclassified pairs, whether an error of this type would be unacceptable in the intended operational use or likely to seriously undermine trust in the method, annotated as yes or no;
- **Unexpected method behaviour:** for misclassified pairs, whether the classification was inconsistent with, or not readily explainable from, the prespecified method logic and recorded decision trace, annotated as yes or no;
- **Incorrect or imprecise input data:** for misclassified pairs, whether incorrect or imprecise input data materially contributed to the classification, annotated as yes or no. This replaces a finer-grained categorization of input-data limitations, since incorrect or imprecise input data is the only such problem expected to be identifiable reliably; and
- **Source of error:** for misclassified pairs, a free-text description of what went wrong. Because errors frequently involve an interaction between method components rather than a single responsible component, reviewers describe the interaction rather than naming one cause, and separately flag **every** contributing stage from a short prespecified list of method components (for this study: evidence extraction, independence assessment, and relationship assessment). The stage flags are not mutually exclusive. Reviewers may additionally identify one or more implicated feature domains from a short prespecified list. Both lists are prespecified and frozen before workbooks are generated, and both are answered by ticking each entry that applies.

For pairs correctly classified by the method of interest, the free-text assessment will briefly describe the decisive evidence supporting the classification. For misclassified pairs, it will describe the apparent root cause. Where the adjudication method and vigiMatch2025 disagree, reviewers will additionally describe the evidence or decision mechanism that most plausibly explains the difference between the methods.

Dimensions that are conditional on a correct classification, misclassification, or disagreement between methods will be displayed only when applicable. Confusion-matrix classifications, joint method classifications, method agreement or disagreement, exact adjudicator outputs, and other quantities derivable from the frozen evaluation results will be populated automatically rather than assessed by the reviewers. A brief interpretation of every question will be presented alongside it within the examination workbook itself.

The two reviewers’ initial assessments will be completed independently. Any disagreement in the structured dimensions or materially different interpretation in the free-text assessments will be considered during arbitration. Inter-reviewer agreement will be reported per structured dimension, with each contributing-stage flag treated as a dimension in its own right and the implicated feature domains as a single set-valued dimension. The original assessments will be retained alongside the consensus assessment.

SCLE findings will not be used to modify the frozen method or silently replace the reference labels used for the primary statistical evaluation. Any cases for which consensus review identifies an incorrect or ambiguous reference label will be reported transparently, and performance using consensus-revised labels may be presented only as an explicitly labelled sensitivity analysis.

### Secondary analyses

Three prespecified secondary analyses will repeat the aggregate precision and recall calculations without repeating SCLE:

1. adjudicator outputs of *Abstain* will be classified as duplicate-positive;
2. adjudicator outputs of *Otherwise related* will be classified as duplicate-positive; and
3. the rule-based adverse-event clinical-picture assessment will be replaced by the prespecified LLM-based assessment, while all remaining method components and decision thresholds remain unchanged.

Each modification will initially be assessed separately against the primary configuration so that its effect can be attributed to the altered classification or component. The number of evaluable pairs and the adjudicator’s classification coverage will be reported for every analysis.