# Host expected replay summary (frozen package)

Package: `ml_training/arm_probe`; frames: 43.

Clean classification (host expected; BPSK real + synthetic 4-class):

| group | n | truth classes | before correct | after correct | before acc | after acc |
|---|---|---|---|---|---|---|
| clean_failure_bpsk | 10 | BPSK | 0 | 10 | 0.0000 | 1.0000 |
| clean_real_bpsk | 10 | BPSK | 6 | 10 | 0.6000 | 1.0000 |
| clean_synthetic_4class | 8 | BPSK,QAM16,QAM64,QPSK | 8 | 8 | 1.0000 | 1.0000 |

**Clean total**: n=28, before 14/28 (0.5000), after 28/28 (1.0000); mixed real BPSK frames and synthetic 4-class frames; not a real-world accuracy estimate

Gate populations (rejection expected; not clean labels):

| group | n | rejected | accepted | reasons |
|---|---|---|---|---|
| gate_clip | 3 | 3 | 0 | {"clipping": 3} |
| gate_noise | 3 | 3 | 0 | {"noise_like": 3} |
| gate_off | 3 | 3 | 0 | {"noise_like": 3} |
| gate_tone | 3 | 3 | 0 | {"tone_like": 3} |

Interference stress (separate from clean accuracy):

- **stress_narrowband**: n=3; after labels ['BPSK', 'BPSK', 'BPSK']; reasons ['', '', '']; interference stress only; not counted in clean 4-class accuracy

Expected values are the frozen host outputs from the package's own runtime;
the ARM replay must match labels/reasons/arbitration exactly and stay within the
documented numeric tolerances (see COMPATIBILITY.md).
