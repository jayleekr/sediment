"""python -m validator.<sub>"""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "loop":
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    from .loop import main
    main()
elif len(sys.argv) > 1 and sys.argv[1] == "lint-e2e":
    from .e2e_runner import lint_e2e_spec
    ok, errs = lint_e2e_spec()
    if not ok:
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("e2e_spec.yaml ✓")
else:
    from .runner import main
    main()
