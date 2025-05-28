import org.mongodb.scala._
import org.mongodb.scala.model.Filters._
import scala.concurrent.Await
import scala.concurrent.duration._
import scala.concurrent.ExecutionContext.Implicits.global
import java.io.PrintWriter
import scala.jdk.CollectionConverters._
import sys.process._

case class User(_id: String, username: String, email: Option[String])
case class Group(_id: String, name: String, members: Seq[String])
case class Expense(_id: String, groupId: Option[String], paidBy: String, amount: Double, participants: Seq[String], description: String)

object SplitwiseReport {
  val mongoClient: MongoClient = MongoClient("mongodb://localhost:27017")
  val database: MongoDatabase = mongoClient.getDatabase("splitwise_db")
  val usersCollection: MongoCollection[Document] = database.getCollection("users")
  val groupsCollection: MongoCollection[Document] = database.getCollection("groups")
  val expensesCollection: MongoCollection[Document] = database.getCollection("expenses")

  // Verify database connection
  def verifyConnection(): Unit = {
    try {
      val databases = Await.result(mongoClient.listDatabaseNames().toFuture(), 10.seconds)
      println(s"Available databases: ${databases.mkString(", ")}")
      if (!databases.contains("splitwise_db")) {
        throw new Exception("Database 'splitwise_db' not found!")
      }
      val collections = Await.result(database.listCollectionNames().toFuture(), 10.seconds)
      println(s"Collections in splitwise_db: ${collections.mkString(", ")}")
      if (!collections.contains("users") || !collections.contains("expenses")) {
        throw new Exception("Required collections 'users' or 'expenses' not found!")
      }
    } catch {
      case e: Exception =>
        println(s"Database connection error: ${e.getMessage}")
        throw e
    }
  }

  def fetchUsers(): Seq[User] = {
    val futureUsers = usersCollection.find().toFuture()
    val docs = Await.result(futureUsers, 10.seconds)
    if (docs.isEmpty) {
      println("No users found in the users collection!")
    }
    docs.foreach { doc =>
      println(s"User document fields: ${doc.keys.mkString(", ")}")
      println(s"User document content: ${doc.toJson()}")
    }
    val users = docs.map { doc =>
      val userId = doc.getObjectId("_id").toString
      val email = Option(doc.getString("email"))
      val username = doc.get("username") match {
        case Some(bson: org.bson.BsonString) => bson.getValue
        case _ => Option(doc.getString("username"))
            .orElse(Option(doc.getString("userName")))
            .orElse(email) // Fallback to email if username is missing
            .getOrElse(userId) // Fallback to userId if email is also missing
      }
      User(userId, username, email)
    }
    println(s"Fetched users: ${users.map(u => s"ID=${u._id}, Username=${u.username}, Email=${u.email.getOrElse("N/A")}").mkString(", ")}")
    users
  }

  def fetchGroups(): Seq[Group] = {
    val futureGroups = groupsCollection.find().toFuture()
    val docs = Await.result(futureGroups, 10.seconds)
    val groups = docs.map(doc => Group(
      doc.getObjectId("_id").toString,
      doc.getString("name"),
      doc.getList("members", classOf[String]).asScala.toSeq
    ))
    println(s"Fetched groups: ${groups.map(g => s"ID=${g._id}, Name=${g.name}, Members=${g.members.mkString(",")}").mkString("; ")}")
    groups
  }

  def fetchExpenses(users: Seq[User]): Seq[Expense] = {
    // Create lookup maps for validation
    val emailToId = users.flatMap(u => u.email.map(email => email -> u._id)).toMap
    val idToUsername = users.map(u => u._id -> u.username).toMap
    val usernameToId = users.map(u => u.username -> u._id).toMap

    val futureExpenses = expensesCollection.find().toFuture()
    val docs = Await.result(futureExpenses, 10.seconds)
    if (docs.isEmpty) {
      println("No expenses found in the expenses collection!")
    }
    docs.foreach { doc =>
      println(s"Expense document fields: ${doc.keys.mkString(", ")}")
      println(s"Expense document content: ${doc.toJson()}")
    }
    val expenses = docs.map { doc =>
      val paidByRaw = doc.get("paidBy") match {
        case Some(bson: org.bson.BsonObjectId) => bson.getValue.toString
        case Some(bson: org.bson.BsonString) => bson.getValue
        case _ => Option(doc.getString("paidBy"))
            .orElse(Option(doc.getString("paid_by")))
            .getOrElse(doc.getObjectId("_id").toString) // Fallback to expense ID
      }
      // Validate paidBy
      val paidBy = if (idToUsername.contains(paidByRaw)) {
        paidByRaw // It's a user ID
      } else if (emailToId.contains(paidByRaw)) {
        emailToId(paidByRaw) // It's an email, map to ID
      } else if (usernameToId.contains(paidByRaw)) {
        usernameToId(paidByRaw) // It's a username, map to ID
      } else {
        println(s"Warning: paidBy '${paidByRaw}' for expense ${doc.getObjectId("_id").toString} does not match any user ID, email, or username.")
        paidByRaw // Use raw value as fallback
      }

      // Validate participants
      val participants = doc.getList("participants", classOf[String]).asScala.toSeq.map { p =>
        if (idToUsername.contains(p)) {
          p // It's a user ID
        } else if (emailToId.contains(p)) {
          emailToId(p) // It's an email, map to ID
        } else if (usernameToId.contains(p)) {
          usernameToId(p) // It's a username, map to ID
        } else {
          println(s"Warning: Participant '${p}' for expense ${doc.getObjectId("_id").toString} does not match any user ID, email, or username.")
          p // Use raw value as fallback
        }
      }

      Expense(
        doc.getObjectId("_id").toString,
        Option(doc.getObjectId("groupId")).map(_.toString),
        paidBy,
        doc.getDouble("amount").doubleValue,
        participants,
        doc.getString("description")
      )
    }
    println(s"Fetched expenses: ${expenses.map(e => s"ID=${e._id}, PaidBy=${e.paidBy}, Amount=${e.amount}, Participants=${e.participants.mkString(",")}").mkString("; ")}")
    expenses
  }

  def calculateBalances(userId: String, expenses: Seq[Expense], users: Seq[User], groups: Seq[Group]): (Map[String, Double], Map[String, Map[String, Double]]) = {
    val emailToId = users.flatMap(u => u.email.map(email => email -> u._id)).toMap
    val idToEmail = users.flatMap(u => u.email.map(email => u._id -> email)).toMap
    val idToUsername = users.map(u => u._id -> u.username).toMap
    val emailToUsername = users.flatMap(u => u.email.map(email => email -> u.username)).toMap
    val usernameToId = users.map(u => u.username -> u._id).toMap
    val groupMap = groups.map(g => g._id -> g.name).toMap

    val individualBalances = scala.collection.mutable.Map[String, Double]().withDefaultValue(0.0)
    val groupBalances = scala.collection.mutable.Map[String, scala.collection.mutable.Map[String, Double]]()

    groups.foreach { g =>
      val memberIds = g.members.map(email => emailToId.getOrElse(email, email))
      val balances = scala.collection.mutable.Map[String, Double]()
      memberIds.foreach { id =>
        val username = idToUsername.getOrElse(id, emailToUsername.getOrElse(idToEmail.getOrElse(id, id), id))
        if (id != userId) {
          balances(username) = 0.0
        }
      }
      groupBalances(g._id) = balances
    }

    expenses.foreach { expense =>
      val share = expense.amount / expense.participants.length
      val isGroupExpense = expense.groupId.isDefined

      // Resolve paidBy to a user ID
      val paidById = if (idToUsername.contains(expense.paidBy)) {
        expense.paidBy
      } else if (emailToId.contains(expense.paidBy)) {
        emailToId(expense.paidBy)
      } else if (usernameToId.contains(expense.paidBy)) {
        usernameToId(expense.paidBy)
      } else {
        expense.paidBy // Fallback to raw value
      }
      val paidByUsername = idToUsername.getOrElse(paidById, emailToUsername.getOrElse(idToEmail.getOrElse(paidById, paidById), paidById))

      if (paidById == userId) {
        expense.participants.filter(p => {
          val pid = if (idToUsername.contains(p)) p else emailToId.getOrElse(p, usernameToId.getOrElse(p, p))
          pid != userId
        }).foreach { participant =>
          val participantId = if (idToUsername.contains(participant)) {
            participant
          } else if (emailToId.contains(participant)) {
            emailToId(participant)
          } else if (usernameToId.contains(participant)) {
            usernameToId(participant)
          } else {
            participant
          }
          val participantUsername = idToUsername.getOrElse(participantId, emailToUsername.getOrElse(participant, participant))
          if (isGroupExpense && groupBalances.contains(expense.groupId.get)) {
            groupBalances(expense.groupId.get)(participantUsername) = groupBalances(expense.groupId.get).getOrElse(participantUsername, 0.0) + share
          } else {
            individualBalances(participantUsername) = individualBalances.getOrElse(participantUsername, 0.0) + share
          }
        }
      } else if (expense.participants.exists(p => {
        val pid = if (idToUsername.contains(p)) p else emailToId.getOrElse(p, usernameToId.getOrElse(p, p))
        pid == userId
      })) {
        val payerUsername = idToUsername.getOrElse(paidById, emailToUsername.getOrElse(paidById, paidById))
        if (isGroupExpense && groupBalances.contains(expense.groupId.get)) {
          groupBalances(expense.groupId.get)(payerUsername) = groupBalances(expense.groupId.get).getOrElse(payerUsername, 0.0) - share
        } else {
          individualBalances(payerUsername) = individualBalances.getOrElse(payerUsername, 0.0) - share
        }
      }
    }

    (individualBalances.toMap, groupBalances.map { case (gid, balances) => groupMap(gid) -> balances.toMap }.toMap)
  }

  def escapeLatex(text: String): String = {
    text.replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
        .replace("_", "\\_")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("~", "\\textasciitilde")
        .replace("^", "\\textasciicircum")
        .replace("\\", "\\textbackslash")
  }

  def generateLatex(userId: String): String = {
    verifyConnection()
    val users = fetchUsers()
    val user = users.find(_._id == userId).getOrElse(throw new Exception(s"User with ID $userId not found. Available user IDs: ${users.map(_._id).mkString(", ")}"))
    val groups = fetchGroups()
    val expenses = fetchExpenses(users)
    val (individualBalances, groupBalances) = calculateBalances(userId, expenses, users, groups)

    val emailToId = users.flatMap(u => u.email.map(email => email -> u._id)).toMap
    val idToEmail = users.flatMap(u => u.email.map(email => u._id -> email)).toMap
    val idToUsername = users.map(u => u._id -> u.username).toMap
    val emailToUsername = users.flatMap(u => u.email.map(email => email -> u.username)).toMap
    val usernameToId = users.map(u => u.username -> u._id).toMap
    val groupMap = groups.map(g => g._id -> g.name).toMap

    val userExpenses = expenses.filter(expense =>
      expense.participants.exists(p => {
        val pid = if (idToUsername.contains(p)) p else emailToId.getOrElse(p, usernameToId.getOrElse(p, p))
        pid == userId
      }) || expense.paidBy == userId
    )

    val latexContent = s"""\\documentclass{article}
\\usepackage{geometry}
\\usepackage{booktabs}
\\usepackage{array}
\\geometry{a4paper, margin=1in}
\\begin{document}
\\section*{Financial Summary for ${escapeLatex(user.username)} (as of May 27, 2025)}

\\subsection*{User Details}
\\begin{tabular}{|l|l|}
\\hline
\\textbf{Field} & \\textbf{Value} \\\\ \\hline
User ID & ${escapeLatex(user._id)} \\\\ \\hline
Username & ${escapeLatex(user.username)} \\\\ \\hline
Email & ${escapeLatex(user.email.getOrElse("N/A"))} \\\\ \\hline
\\end{tabular}

\\subsection*{Expenses Involving ${escapeLatex(user.username)}}
${if (userExpenses.isEmpty) "No expenses found." else s"""\\begin{tabular}{|l|l|r|l|l|}
\\hline
\\textbf{Expense ID} & \\textbf{Paid By} & \\textbf{Amount (INR)} & \\textbf{Participants} & \\textbf{Description} \\\\ \\hline
${userExpenses.map { expense =>
  val paidById = if (idToUsername.contains(expense.paidBy)) expense.paidBy else emailToId.getOrElse(expense.paidBy, usernameToId.getOrElse(expense.paidBy, expense.paidBy))
  val paidByUsername = idToUsername.getOrElse(paidById, emailToUsername.getOrElse(expense.paidBy, expense.paidBy))
  val participants = expense.participants.map(p => {
    val pid = if (idToUsername.contains(p)) p else emailToId.getOrElse(p, usernameToId.getOrElse(p, p))
    idToUsername.getOrElse(pid, emailToUsername.getOrElse(p, p))
  }).mkString(", ")
  s"${escapeLatex(expense._id)} & ${escapeLatex(paidByUsername)} & ${f"${expense.amount}%.2f"} INR & ${escapeLatex(participants)} & ${escapeLatex(expense.description)} \\\\ \\hline"
}.mkString("\n")}
\\end{tabular}"""}

\\subsection*{Individual Balances}
${if (individualBalances.isEmpty) "No individual balances to display." else s"""\\begin{tabular}{|l|r|}
\\hline
\\textbf{Person} & \\textbf{Amount (INR)} \\\\ \\hline
${individualBalances.map { case (person, amount) =>
  s"${escapeLatex(person)} & ${if (amount >= 0) f"You are owed $amount%.2f INR" else f"You owe ${-amount}%.2f INR"} \\\\ \\hline"
}.mkString("\n")}
\\end{tabular}"""}

${groupBalances.map { case (groupName, balances) =>
  s"""\\subsection*{Group: ${escapeLatex(groupName)}}
${if (balances.isEmpty) "No balances to display for this group." else s"""\\begin{tabular}{|l|r|}
\\hline
\\textbf{Person} & \\textbf{Amount (INR)} \\\\ \\hline
${balances.map { case (person, amount) =>
    s"${escapeLatex(person)} & ${if (amount >= 0) f"You are owed $amount%.2f INR" else f"You owe ${-amount}%.2f INR"} \\\\ \\hline"
  }.mkString("\n")}
\\end{tabular}"""}"""
}.mkString("\n")}

\\end{document}
"""
    latexContent
  }

  def main(args: Array[String]): Unit = {
    if (args.length != 1) {
      throw new IllegalArgumentException("Please provide exactly one argument: the user ID")
    }
    val userId = args(0)
    val latexContent = generateLatex(userId)

    val outputDir = new java.io.File("output")
    if (!outputDir.exists()) {
      outputDir.mkdirs()
    }

    val outputFile = new PrintWriter("output/FinancialSummary.tex")
    try {
      outputFile.write(latexContent)
    } finally {
      outputFile.close()
    }
    println(s"LaTeX file written to output/FinancialSummary.tex")

    try {
      val compileCommand = Seq("latexmk", "-pdf", "-output-directory=output", "output/FinancialSummary.tex")
      val exitCode = compileCommand.!
      if (exitCode == 0) {
        println("PDF generated successfully at output/FinancialSummary.pdf")
      } else {
        throw new RuntimeException(s"Failed to compile LaTeX to PDF. Exit code: $exitCode")
      }
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Error compiling LaTeX to PDF: ${e.getMessage}")
    }
  }
}