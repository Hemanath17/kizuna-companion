from langchain_core.messages import HumanMessage
from graph.build_graph import kizuna_graph

TEST_USER_ID = "ff91400a-d2f0-407e-9b6d-3671c5800d61"

def main():
    history = []
    print("Kizuna (local test mode). Type 'quit' to exit.")
    print(f"Using test user_id: {TEST_USER_ID}\n")

    while True:
        user_input = input("You: ")
        if user_input.lower() in ("quit", "exit"):
            break
        history.append(HumanMessage(content=user_input))

        result = kizuna_graph.invoke({
            "messages": history,
            "user_id": TEST_USER_ID,
        })

        print(f"Kizuna: {result['final_response']}\n")

if __name__ == "__main__":
    main()