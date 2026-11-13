# Build the report. Run from paper/paper-artifacts/.
#   make          build main.pdf
#   make report   build and copy it to ../GRAFT-report.pdf (the deliverable)
#   make overleaf rebuild overleaf.zip from the current sources
#   make check    pre-submission checks
SOURCES = main.tex macros.tex references.bib $(wildcard sections/*.tex) $(wildcard figures/*.tex)

main.pdf: $(SOURCES)
	pdflatex -interaction=nonstopmode main
	-bibtex main   # "-": no \cite yet must not stop the build
	pdflatex -interaction=nonstopmode main
	pdflatex -interaction=nonstopmode main

report: main.pdf
	cp main.pdf ../GRAFT-report.pdf

overleaf: $(SOURCES)
	rm -f overleaf.zip
	zip -q -X overleaf.zip $(SOURCES)

check: main.pdf
	sh scripts/check.sh

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.toc main.pdf

.PHONY: report overleaf check clean
