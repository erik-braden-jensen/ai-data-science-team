# BUSINESS SCIENCE UNIVERSITY
# AI DATA SCIENCE TEAM
# ***
# * Agents: Data Cleaning Agent

# Libraries
from typing import TypedDict, Annotated, Sequence, Literal
import operator

from langchain.prompts import PromptTemplate
from langchain_core.messages import BaseMessage

from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

import os
import io
import pandas as pd

from ai_data_science_team.templates.agent_templates import(
    node_func_execute_agent_code_on_data, 
    node_func_human_review,
    node_func_fix_agent_code, 
    node_func_explain_agent_code, 
    create_coding_agent_graph
)
from ai_data_science_team.tools.parsers import PythonOutputParser
from ai_data_science_team.tools.regex import relocate_imports_inside_function

# Setup

LOG_PATH = os.path.join(os.getcwd(), "logs/")

# Agent

def make_data_cleaning_agent(model, log=False, log_path=None, human_in_the_loop=False):
    """
    Creates a data cleaning agent that can be run on a dataset. The agent can be used to clean a dataset in a variety of
    ways, such as removing columns with more than 40% missing values, imputing missing
    values with the mean of the column if the column is numeric, or imputing missing
    values with the mode of the column if the column is categorical.
    The agent takes in a dataset and some user instructions, and outputs a python
    function that can be used to clean the dataset. The agent also logs the code
    generated and any errors that occur.
    
    The agent is instructed to to perform the following data cleaning steps:
    
    - Removing columns if more than 40 percent of the data is missing
    - Imputing missing values with the mean of the column if the column is numeric
    - Imputing missing values with the mode of the column if the column is categorical
    - Converting columns to the correct data type
    - Removing duplicate rows
    - Removing rows with missing values
    - Removing rows with extreme outliers (3X the interquartile range)
    - User instructions can modify, add, or remove any of the above steps

    Parameters
    ----------
    model : langchain.llms.base.LLM
        The language model to use to generate code.
    log : bool, optional
        Whether or not to log the code generated and any errors that occur.
        Defaults to False.
    log_path : str, optional
        The path to the directory where the log files should be stored. Defaults to
        "logs/".
    human_in_the_loop : bool, optional
        Whether or not to use human in the loop. If True, adds an interput and human in the loop step that asks the user to review the data cleaning instructions. Defaults to False.
        
    Examples
    -------
    ``` python
    import pandas as pd
    from langchain_openai import ChatOpenAI
    from ai_data_science_team.agents import data_cleaning_agent
    
    llm = ChatOpenAI(model = "gpt-4o-mini")

    data_cleaning_agent = make_data_cleaning_agent(llm)
    
    df = pd.read_csv("https://raw.githubusercontent.com/business-science/ai-data-science-team/refs/heads/master/data/churn_data.csv")
    
    response = data_cleaning_agent.invoke({
        "user_instructions": "Don't remove outliers when cleaning the data.",
        "data_raw": df.to_dict(),
        "max_retries":3, 
        "retry_count":0
    })
    
    pd.DataFrame(response['data_cleaned'])
    ```

    Returns
    -------
    app : langchain.graphs.StateGraph
        The data cleaning agent as a state graph.
    """
    llm = model
    
    # Setup Log Directory
    if log:
        if log_path is None:
            log_path = LOG_PATH
        if not os.path.exists(log_path):
            os.makedirs(log_path)    

    # Define GraphState for the router
    class GraphState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], operator.add]
        user_instructions: str
        recommended_steps: str
        data_raw: dict
        data_cleaner_function: str
        data_cleaner_error: str
        data_cleaned: dict
        max_retries: int
        retry_count: int

    
    def recommend_cleaning_steps(state: GraphState):
        """
        Recommend a series of data cleaning steps based on the input data. 
        These recommended steps will be appended to the user_instructions.
        """
        print("---DATA CLEANING AGENT----")
        print("    * RECOMMEND CLEANING STEPS")

        # Prompt to get recommended steps from the LLM
        recommend_steps_prompt = PromptTemplate(
            template="""
            You are a Data Cleaning Expert. Given the following information about the data, 
            recommend a series of numbered steps to take to clean and preprocess it. 
            The steps should be tailored to the data characteristics and should be helpful 
            for a data cleaning agent that will be implemented.
            
            General Steps:
            Things that should be considered in the data cleaning steps:
            
            * Removing columns if more than 40 percent of the data is missing
            * Imputing missing values with the mean of the column if the column is numeric
            * Imputing missing values with the mode of the column if the column is categorical
            * Converting columns to the correct data type
            * Removing duplicate rows
            * Removing rows with missing values
            * Removing rows with extreme outliers (3X the interquartile range)
            
            Custom Steps:
            * Analyze the data to determine if any additional data cleaning steps are needed.
            * Recommend steps that are specific to the data provided. Include why these steps are necessary or beneficial.
            * If no additional steps are needed, simply state that no additional steps are required.
            
            IMPORTANT:
            Make sure to take into account any additional user instructions that may add, remove or modify some of these steps. Include comments in your code to explain your reasoning for each step. Include comments if something is not done because a user requested. Include comments if something is done because a user requested.
            
            User instructions:
            {user_instructions}
            
            Previously Recommended Steps (if any):
            {recommended_steps}
            
            Data Sample (first 50 rows):
            {data_head}
            
            Data Description:
            {data_description}
            
            Data Info:
            {data_info}

            Return the steps as a bullet point list (no code, just the steps).
            """,
            input_variables=["user_instructions", "data_head","data_description","data_info"]
        )

        data_raw = state.get("data_raw")
        df = pd.DataFrame.from_dict(data_raw)

        buffer = io.StringIO()
        df.info(buf=buffer)
        info_text = buffer.getvalue()

        steps_agent = recommend_steps_prompt | llm
        recommended_steps = steps_agent.invoke({
            "user_instructions": state.get("user_instructions"),
            "recommended_steps": state.get("recommended_steps"),
            "data_head": df.head(50).to_string(),
            "data_description": df.describe().to_string(),
            "data_info": info_text
        }) 
        
        return {"recommended_steps": "\n\n# Recommended Steps:\n" + recommended_steps.content.strip()}
    
    def create_data_cleaner_code(state: GraphState):
        print("    * CREATE DATA CLEANER CODE")
        
        data_cleaning_prompt = PromptTemplate(
            template="""
            You are a Data Cleaning Agent. Your job is to create a data_cleaner() function that can be run on the data provided using the following recommended steps.
            
            Recommended Steps:
            {recommended_steps}
            
            You can use Pandas, Numpy, and Scikit Learn libraries to clean the data.
            
            Use this information about the data to help determine how to clean the data:

            Sample Data (first 100 rows):
            {data_head}
            
            Data Description:
            {data_description}
            
            Data Info:
            {data_info}
            
            Return Python code in ```python ``` format with a single function definition, data_cleaner(data_raw), that incldues all imports inside the function. 
            
            Return code to provide the data cleaning function:
            
            def data_cleaner(data_raw):
                import pandas as pd
                import numpy as np
                ...
                return data_cleaned
            
            Best Practices and Error Preventions:
            
            Always ensure that when assigning the output of fit_transform() from SimpleImputer to a Pandas DataFrame column, you call .ravel() or flatten the array, because fit_transform() returns a 2D array while a DataFrame column is 1D.
            
            """,
            input_variables=["recommended_steps", "data_head", "data_description", "data_info"]
        )

        data_cleaning_agent = data_cleaning_prompt | llm | PythonOutputParser()
        
        data_raw = state.get("data_raw")
        
        df = pd.DataFrame.from_dict(data_raw)
        
        buffer = io.StringIO()
        df.info(buf=buffer)
        info_text = buffer.getvalue()
        
        response = data_cleaning_agent.invoke({
            "user_instructions": state.get("user_instructions"),
            "recommended_steps": state.get("recommended_steps"),
            "data_head": df.head().to_string(), 
            "data_description": df.describe().to_string(), 
            "data_info": info_text
        })
        
        response = relocate_imports_inside_function(response)
        
        # For logging: store the code generated:
        if log:
            with open(log_path + 'data_cleaner.py', 'w') as file:
                file.write(response)
   
        return {"data_cleaner_function" : response}
    
    def human_review(state: GraphState) -> Command[Literal["recommend_cleaning_steps", "create_data_cleaner_code"]]:
        return node_func_human_review(
            state=state,
            prompt_text="Is the following data cleaning instructions correct? (Answer 'yes' or provide modifications)\n{steps}",
            yes_goto="create_data_cleaner_code",
            no_goto="recommend_cleaning_steps",
            user_instructions_key="user_instructions",
            recommended_steps_key="recommended_steps"            
        )
    
    def execute_data_cleaner_code(state):
        return node_func_execute_agent_code_on_data(
            state=state,
            data_key="data_raw",
            result_key="data_cleaned",
            error_key="data_cleaner_error",
            code_snippet_key="data_cleaner_function",
            agent_function_name="data_cleaner",
            pre_processing=lambda data: pd.DataFrame.from_dict(data),
            post_processing=lambda df: df.to_dict(),
            error_message_prefix="An error occurred during data cleaning: "
        )
        
    def fix_data_cleaner_code(state: GraphState):
        data_cleaner_prompt = """
        You are a Data Cleaning Agent. Your job is to create a data_cleaner() function that can be run on the data provided. The function is currently broken and needs to be fixed.
        
        Make sure to only return the function definition for data_cleaner().
        
        Return Python code in ```python``` format with a single function definition, data_cleaner(data_raw), that includes all imports inside the function.
        
        This is the broken code (please fix): 
        {code_snippet}

        Last Known Error:
        {error}
        """

        return node_func_fix_agent_code(
            state=state,
            code_snippet_key="data_cleaner_function",
            error_key="data_cleaner_error",
            llm=llm,  
            prompt_template=data_cleaner_prompt,
            log=log,
            log_path=log_path,
            log_file_name="data_cleaner.py"
        )
    
    def explain_data_cleaner_code(state: GraphState):        
        return node_func_explain_agent_code(
            state=state,
            code_snippet_key="data_cleaner_function",
            result_key="messages",
            error_key="data_cleaner_error",
            llm=llm,  
            role="data_cleaning_agent",
            explanation_prompt_template="""
            Explain the data cleaning steps that the data cleaning agent performed in this function. 
            Keep the summary succinct and to the point.\n\n# Data Cleaning Agent:\n\n{code}
            """,
            success_prefix="# Data Cleaning Agent:\n\n ",
            error_message="The Data Cleaning Agent encountered an error during data cleaning. Data could not be explained."
        )
        
    # Define the graph
    node_functions = {
        "recommend_cleaning_steps": recommend_cleaning_steps,
        "human_review": human_review,
        "create_data_cleaner_code": create_data_cleaner_code,
        "execute_data_cleaner_code": execute_data_cleaner_code,
        "fix_data_cleaner_code": fix_data_cleaner_code,
        "explain_data_cleaner_code": explain_data_cleaner_code
    }
    
    app = create_coding_agent_graph(
        GraphState=GraphState,
        node_functions=node_functions,
        recommended_steps_node_name="recommend_cleaning_steps",
        create_code_node_name="create_data_cleaner_code",
        execute_code_node_name="execute_data_cleaner_code",
        fix_code_node_name="fix_data_cleaner_code",
        explain_code_node_name="explain_data_cleaner_code",
        error_key="data_cleaner_error",
        human_in_the_loop=human_in_the_loop,  # or False
        human_review_node_name="human_review",
        checkpointer=MemorySaver() if human_in_the_loop else None
    )
        
    return app
