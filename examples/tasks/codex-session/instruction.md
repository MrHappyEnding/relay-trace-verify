Fix `positive_sum` so it adds only positive values and returns zero for empty
input, including iterables. RTV's probe supplies a controlled two-stage prompt:
first retain an empty-input defect, then complete the solution. This measures
session transfer mechanics, not benchmark problem-solving performance.
