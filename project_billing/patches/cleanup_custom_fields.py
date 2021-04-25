import frappe

def execute():
    frappe.delete_doc_if_exists('Custom Field', 'Sales Invoice-past_billing_details')
    frappe.delete_doc_if_exists('Custom Field', 'Sales Invoice-project_retention_amount')

