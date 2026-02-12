![Draft for review only](/assets/img/draft_for_review.svg)

# quantity

![quantity Diagram](../diagrams/i72__quantity.dot.svg)

<a href="../../diagrams/i72__quantity.dot.svg">Open interactive quantity diagram</a>

## Specializations of quantity

| Class | Description |
|-------|-------------|
| [Cardinality (i72)](i72__Cardinality.md) |  |
| [Difference Indicator (i72)](i72__DifferenceIndicator.md) |  |
| [Distinct_count (i72)](i72__Distinct_count.md) |  |
| [Indicator (i72)](i72__Indicator.md) |  |
| [Mean (i72)](i72__Mean.md) |  |
| [Parameter (i72)](i72__Parameter.md) |  |
| [Ratio Indicator (i72)](i72__RatioIndicator.md) |  |
| [Standard_deviation (i72)](i72__Standard_deviation.md) |  |
| [Sum (i72)](i72__Sum.md) |  |
| [Sum Indicator (i72)](i72__SumIndicator.md) |  |

## Formalization for quantity

| Property | Constraint |
|----------|------------|
| hasUnit | all Unit_of_measure |
| hasUnit | exactly 1 owl::Thing |
| hasValue | all Measure |
| hasValue | exactly 1 owl::Thing |
| subClassOf | ISO21972Thing |
| unit_of_measure | all Unit_of_measure |
| unit_of_measure | exactly 1 owl::Thing |
| value | all Measure |
| value | exactly 1 owl::Thing |

