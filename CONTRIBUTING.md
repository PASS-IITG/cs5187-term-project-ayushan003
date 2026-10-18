# Contributing (team rules)

1. **Commit only from your own machine, under your own identity.** Lab PCs only `git pull`.
   Use the email linked to your GitHub account so commits are attributed to you.
2. **Branch → pull request → partner review → merge.** Branch names: `feat/<stage>-<thing>`,
   `fix/<thing>`. Every PR gets at least one real review comment from the partner.
3. **Merge with "Create a merge commit" or "Rebase and merge"**, never "Squash".
4. **One logical change per commit, together with its test.** Keep commits small.
5. **Message format:** `type(scope): what changed`, where type is one of
   `feat fix test build refactor data exp docs`. Reference issues (`Closes #12`).
   Pair work: add a `Co-authored-by: Name <email>` trailer.
6. **Generated output goes in separate `data:` commits:** harnesses, `api.json`, `gap.json`,
   LLM cache and results.
7. **Each fuzzing campaign gets an `exp:` commit** appending a row to
   `src/results/<lib>/campaigns.csv` (start, end, commit hash, machines, harness set, config).
8. **Never commit crash inputs before the upstream fix is public.** Keep them in `src/private/`
   (gitignored).
9. **Disclose AI assistance** as the course policy requires (contribution statement in the report).
10. `make test` must pass before you open a PR.
