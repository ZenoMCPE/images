package main

type Result enum {
	Ok { value int }
	Err { message string }
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
}
