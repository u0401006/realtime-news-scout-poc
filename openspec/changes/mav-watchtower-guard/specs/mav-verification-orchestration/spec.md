# Spec: MAV Verification Orchestration

## Capability: mav-verification-orchestration

## ADDED Requirements

### Requirement: multi-agent-voting
The system MUST integrate MAV-Council for three-model voting to determine the public value and eligibility of news articles.

#### Scenario: verify-public-value
- Given: A high-score news article from Scorer (Score > 94.0)
- When: Triggering MAV three-model voting
- Then: MUST receive at least 2/3 majority with `eligible: true` to be published.

### Requirement: clickbait-veto
The system MUST have a veto power against clickbait headlines.

#### Scenario: veto-content-farm
- Given: A headline with "content farm" or "PR response" characteristics
- When: MAV detects `clickbait: true`
- Then: MUST set `eligible` to `false` regardless of Scorer score.
