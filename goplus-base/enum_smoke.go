package main

import (
	"bytes"
	"encoding/gob"
)

type Result enum {
	Ok { value int }
	Err { message string }
}

type ItemKind enum {
	None
}

func main() {
	var result Result = Ok{value: 42}
	switch result {
	case Ok:
		if result.value != 42 {
			panic("unexpected enum payload")
		}
	case Err, nil:
		panic("unexpected enum variant")
	}

	var kind ItemKind = None{}
	var encoded bytes.Buffer
	if err := gob.NewEncoder(&encoded).Encode(map[string]any{"kind": kind}); err != nil {
		panic(err)
	}
}
