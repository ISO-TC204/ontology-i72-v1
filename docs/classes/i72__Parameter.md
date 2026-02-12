![Draft for review only](/assets/img/draft_for_review.svg)

# Parameter

![Parameter Diagram](../diagrams/i72__Parameter.dot.svg)

<a href="../../diagrams/i72__Parameter.dot.svg">Open interactive Parameter diagram</a>

## Specializations of Parameter

| Class | Description |
|-------|-------------|
| [Cardinality (i72)](i72__Cardinality.md) |  |
| [Distinct_count (i72)](i72__Distinct_count.md) |  |
| [Mean (i72)](i72__Mean.md) |  |
| [Standard_deviation (i72)](i72__Standard_deviation.md) |  |
| [Sum (i72)](i72__Sum.md) |  |

## Formalization for Parameter

| Property | Constraint |
|----------|------------|
| parameter_of_var | exactly 1 owl::Thing |
| subClassOf | ISO21972Thing |
| subClassOf | Quantity |

## Used by classes

| Class | Property |
|-------|----------|
| [Population (i72)](i72__Population.md) | is_described_by |
| [Statistic (i72)](i72__Statistic.md) | is_an_estimate_of |

