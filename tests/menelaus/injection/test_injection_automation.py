import numpy as np
import pandas as pd
from menelaus.injection.injection_automation import InjectionTesting


def test_brownian_noise():
    df = pd.DataFrame(np.random.rand(100, 5), columns=["a", "b", "c", "d", "e"])
    tester = InjectionTesting(df)
    start = 0.75
    end = 1

    col = tester.inject_random_brownian_noise(50, start=start, end=end, num_drift_cols=1)
    std_normal = (tester.df.iloc[0 : int(start * len(df)), ][col].std().iloc[0, ])
    std_drift = (tester.df.iloc[int(start * len(df)) + 1:int(end * len(df)), ][col].std().iloc[0, ])

    assert std_drift > std_normal


def test_class_manipulation():
    df = pd.DataFrame(np.random.choice(a=["a", "b", "c"], size=100, p=[0.4, 0.3, 0.3]))
    swap_tester = InjectionTesting(df)
    join_tester = InjectionTesting(df)
    start = 0
    end = 1

    cols, all_swap_classes = swap_tester.inject_random_class_manipulation(
        manipulation_type="class_swap", start=start, end=end
    )
    col = cols[0]
    swap_classes = all_swap_classes[0]

    assert len(df[df[col] == swap_classes[0]]) == len(swap_tester.df[swap_tester.df[col] == swap_classes[1]])
    assert len(df[df[col] == swap_classes[1]]) == len(swap_tester.df[swap_tester.df[col] == swap_classes[0]])

    cols, all_join_classes = join_tester.inject_random_class_manipulation(manipulation_type="class_join", start=start, end=end)
    col = cols[0]
    join_classes = all_join_classes[0]

    assert len(join_tester.df[join_tester.df[col] == join_classes[0]]) == 0
    assert len(join_tester.df[join_tester.df[col] == join_classes[1]]) == 0
